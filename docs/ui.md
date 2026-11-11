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
- The shell: a top bar across the window with the Evlet brand and the Workspace switcher at its left,
  then at its right the search button, whose palette opens the Explorer ([below](#top-bar-search-and-explorer)),
  a history button for recent records, notifications and shortcuts; under it a white sidebar with the workspace's
  sections and shortcuts ([below](#sidebar-and-workspaces)) beside a light grey main area whose cards are white.
  Each page's breadcrumb is a small line above its title ([Breadcrumb](#breadcrumb)). New records start from the New button
  on a model's list page or from the search palette.
- Generated model screens: lists, boards for models that name their State in `Meta.board`, record views (on their own
  page or in a side panel beside the list, `Meta.open_in`), new records, actions, moves, void and revise, unpost and repost, child tables, measures, related records and
  files.
- Reports ([reports.md](reports.md)): filters, typed rows, totals, a chart, Export and Print at
  `/reports/<name>`, listed in the sidebar and in the Explorer.
- Slips ([slips.md](slips.md)): forms presented from a record, an action, another slip or browser
  code, checked on the server and handed in to a handler or turned into a record.
- Printing ([printing.md](printing.md)): Print in a record's More menu for roles that may print the
  model, when the server has the print extra. It opens a drawer with the server's PDF in the browser's
  own viewer, a choice of design, Download, and File a copy on records that keep attachments.

Tables keep per-person column choices. The Columns menu of a record's child tables and of a report
orders columns by dragging (or the keyboard), shows or hides them, and pins any number on the left:
columns above the menu's divider stay in place while the table scrolls sideways. Prefs saved before
pinning keep the first column pinned. A child table's Focus view keeps the record's header and its
save bar in view ([ui-framework.md](ui-framework.md)).

A Table the record fills itself (`Meta.derives`, [model-engine.md](model-engine.md#derived-values))
is read only on the record page, in focus view and in the row editor, whatever the state: it reads
"Filled in by the record when it is saved." and a save never sends its rows. Its fields show read
only too, and it has no editing controls: no selection checkboxes, drag handles or Edit, only the row
number (ChildTable `derived`).

Model `Meta` options shape these screens: `list_fields`, `title_field`, `search_fields`,
`layout` (sections, or tabs and blocks, see Record layouts below), `key_fields`, `cards`, `relations`, `measures`, `open_in`, `list_tabs`,
`list_tab`, `autosave`, `status`, `board`, `summary`, `side_blocks`, `panel`, `panel_summary`, `focus` and `duplicate`. See "What the model decides" in [ui-framework.md](ui-framework.md).

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

### Record title row

A record opens with one title row under the breadcrumb: the model's icon on a tinted square tile (a
person's avatar for a person model), the title, and the status badges on the title's line. On the
right, centred on the row, are up to two outline buttons (the model's actions and slips, then Ask
Eva), the main move as a solid split button, and More. The split button runs the first move this
person may take now; its chevron lists the other moves, and with one move there is no chevron. A
model without a State or lifecycle has no split button. Everything that does not fit, Print and the
record's own items (Copy link, Duplicate, Delete) are in More. A muted subtitle under the title has the
first two key fields with a value; a line under the row says when a name or number is still to be assigned
and, for a confirmable record, its revision ("Confirmed · Revision 2"). A new record's row has Cancel and Create instead. In a side panel the row keeps one outline
button, and when the header is narrow (a side panel, a phone) the buttons wrap onto their own line
under the title. Records are edited in place, so there is no Edit button.

### Each fact once on the record page

A record page shows each value once per view. The header has the title, the name, the State and, as its
subtitle, the first two key fields the primary area does not show. The key cards (or, without them, the summary
strip) have calculated values and per-record measures the header doesn't show; with nothing left, there is no strip.
The form of a saved record leaves out what the header and the cards or strip show: the title, name and State, a
record number nothing can edit (the breadcrumb, or in a side panel the subtitle, shows it), and the cards' or
strip's fields. While a side panel hides the cards or strip (`Meta.panel_summary = False`), the form keeps their
fields. So a section left with only such fields, such as Other holding the number, does not show. A new record's
form keeps everything. Related records are a row of "more information"
that counts each relation while closed and lists every one when open. A slim footer under the form has
Created and Modified (who and when), the record ID with a copy button, and a Copy link button for the
record's address; More has Copy link and Copy record ID too, and More > Duplicate starts a copy
([below](#duplicate)). The side column is 370 px wide.

### Duplicate

The More menu of a saved record has **Duplicate** (Alt+Shift+D, ⌥⇧D on a Mac), on its page and in a
side panel. It shows only when the role may create records of the model, as the list's New button
does: not on a read-only model (`Meta.api = 'read'`) or for a role without create. Ledger models
have no record page. When the server provides it, `"duplicate": false` on a model's schema entry
turns Duplicate off for that model (no menu item, and Alt+Shift+D does nothing); without the key
Duplicate is allowed.

Duplicate opens a new, unsaved record of the same model where the source was: on the page, or in
the side panel. Its form is filled in from the saved record, held in memory (never in the address),
with a banner "Copied from ... Nothing is saved until you select Create." Nothing is saved, and no
number is used, until Create, which sends a create (POST) like any new record.

- **Copied:** the visible, stored fields the role may write, and the rows of every table at every
  depth, without their ids, so they are created new on save. Values of a locked or posted record
  are copied too: duplicating a Done project gives a new Draft project with its fields.
- **Left out:** the id, the name or number the server assigns (the naming field, Sequence fields),
  the State (the copy starts in the default state), calculated and read-only fields (they
  recalculate), fields the role cannot write, files and the Journal. Fields marked `unique` are
  left empty, and the banner names them: "Unique fields were left empty: Code." When the server
  provides it, `"copy": false` on a field or on a Table entry leaves it out too; without the key it
  is copied. A field left out starts as on any new record (empty, or its default), and a table left
  out starts with no rows.
- **Saved values.** Duplicate copies the record as saved; edits not yet saved stay with it, and
  leaving it asks or keeps them as usual.
- **Cursor and title.** Nothing is added to the title. The cursor starts where it does on any new
  form: `Meta.focus`, or the first editable field.
- **Unsaved.** The copy has values, so it counts as unsaved from the start: leaving it keeps it as
  an unfinished new record or asks, as described in [Unsaved changes](#unsaved-changes). It does
  not offer to restore an earlier unfinished draft.

Only the main area scrolls, on every page: the sidebar and the top bar stay in place. On a record
page the side column stays in place too while the main column scrolls, and scrolls on its own when
it is taller than the window. In narrow windows and on a phone it sits under the main column and
scrolls with it.

A record page has no tabs (removed in 0.4): every record uses the [compact record page](#compact-record-page).
Its content comes first; Activity is in the side column, which a narrow window opens as a drawer.

Two options change this per model:

```python
class Project(Model):
    class Meta:
        summary = False                              # no summary strip
        side_blocks = ('related', 'files')           # keep the automatic Related row and the attachments
```

`side_blocks` takes `key_fields`, `related`, `files` and `info`, each once. On the record page `files`
keeps the attachments in the side column (without it they go), and `related` keeps the automatic Related
row when the layout lists related records itself; `key_fields` and `info` are accepted and change nothing
(key fields are the subtitle, record info is the footer). Anything else, or a `summary` that is not True
or False, fails when the registry is built.

### How a record's lifecycle reads

The State in the schema decides how a record's state is drawn, never the state's name:

- **Cancelled**: a state the State lists in `cancelled` (`State(..., cancelled=('cancelled', 'lost'))`, sent as
  `"cancelled"` on the state field, the void state included). A schema without that key falls back to a `final`
  state declared in red (`colors={'cancelled': 'rose'}`), or the framework's `void` state of a State with `voidable`
  states. In every list (the table, the narrow list beside a panel and the board) its
  first cell is struck through in grey after a ban icon and the row is muted a little; its status badge keeps its
  colour. On the record the title is struck through in grey, the State reads as red "Cancelled" text with a ban
  icon (no pill), and a soft red strip says "Cancelled on 09-11-2026 by sara. Reason: ...", from the newest move
  into that state in the journal (its reason, or a text value the move asked for). Without a journal the strip
  says only the state.
- **Locked**: any other `locked` or `final` state, such as Confirmed or Done: a lock and the state's name in grey.
  A separate `Meta.status` badge after it stays a soft pill ("Paid").
- In lists (the table, the narrow list beside a panel and board cards), a small muted icon before the first value
  says the same: a ban for cancelled, a lock for locked or final, a pencil for a draft; only cancelled rows are
  struck through and greyed. A list of a model with a State keeps the icon's room on every row, so the first
  column stays aligned.
- **Draft**: the state a record starts in (the State's `default`, else its first state) while not locked: a dashed
  pill with a pencil.
- Any other state keeps the coloured pill. The badges still open the moves this person may take.

Without the `cancelled` list a `final` state that is not red (or a model without colours) reads as Locked, since it
may as well be a closed or won record.

### What a side panel shows

A list that opens records in a side panel (`Meta.open_in = 'panel'`, or the Controls list view)
can show less of each record than the full page. Each app decides which parts:

```python
class Ticket(Model):
    notes = LongText(panel=False)            # this field stays on the full page
    lines = Table(Line, panel=False)         # so does this table

    class Meta:
        open_in = 'panel'
        layout = (Section('Ticket', ('subject', 'owner')),
                  Section('Internal', ('audit_note',), panel=False))   # and this section
```

`panel=False` works on a field, a `Section` and a `Table`. `Meta.panel_summary = False` leaves out the
summary strip, or on a compact page the key cards (`Meta.cards`); the full page still shows them:

```python
    class Meta:
        cards = ('total', 'parcels')
        panel_summary = False                   # key cards on the full page only
```

For a model where only a few parts
matter in the panel, `Meta.panel` names what to keep, in order, and hides everything else:

```python
    class Meta:
        panel = ('Ticket', 'related', 'info')   # a section, the Related row and Record info
```

Names are section titles, field names, `Table` field names, the record's parts (`activity`,
`details`, `revisions`, `related`, `postings`), `summary` (the summary strip) and the side blocks
(`key_fields`, `files`, `info`). Naming a section keeps all its fields; naming a field keeps its
section with only that field; `details` keeps every section. The schema sends the hidden parts and
Table fields as the panel's `tabs`. A section or field of the same name wins over a part or block. When both
are given, `Meta.panel` wins over the flags (it keeps the strip or cards only when it names `summary`). An unknown
name, a name given twice or a flag that is not True or False fails when the registry is built.

- **Display only.** Hiding is never a permission: the API, row, field and action checks are
  unchanged, and the full page shows everything.
- **A new record can always be saved.** A required field that is empty on a new record shows in
  the panel even when it is hidden.
- **The full page is one click away.** When the panel hides anything, a line under the record says
  so with an Open full page link, as well as the panel's Expand button.
- **One view.** The full page and the side panel draw the same record view (title row, banners, the compact
  card, side column and footer); `Meta.panel` only filters it. The view is responsive to its own width, not the
  window's: in a narrow frame (a side panel, a narrow window) there is no side column, and the drawer button at the
  end of the breadcrumb row (in a panel, the title row, before Expand and Close) opens it. A layout change therefore
  applies to both.
- **The panel header.** The record image (when the model has one), the title, the status badge (also for a State
  shown as a bar: the panel has no stage bar), a lock while the record can't be changed, then the drawer button,
  Expand and Close. Under the title a muted line has the record's number and, on a compact view, its first two key
  fields with a value ("Desert Rose Retail · 08-11-2026"). While the panel covers the list, a line above it has
  the Back button instead of Expand and Close.
- **A compact record in the panel is one white section**, like a document preview: the header, a thin line, the key
  cards as plain label-over-value pairs, the Main fields two to a row and its tables, then more information. No
  boxes inside it. It is the same view as the page, styled by the panel.
- Controls, Models, Form layout can change it per instance with the panel button on each section,
  field and part, and the Side panel card for the summary strip and blocks
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

### Section rows

A `Table(..., sections=True)` may hold section rows, headings that group the rows below them
([model-engine.md](model-engine.md#section-rows)). The schema's `children` entry carries
`"sections": true` and `"section_sums"`: the row fields each section row sums over its rows, which
are the fields the table's total line sums. The record's `children` list the section rows in place
as `{"id", "section"}`; the browser computes the sums from the rows, and nothing is stored.

The table editor does not draw them yet: it leaves section rows out of the grid, and its change
sets keep them where they are. Paging (`lines/`) counts real rows only. Duplicate copies only the
rows, so a duplicate starts without the sections. A child row model's own `Meta.layout` (`Section`
blocks with titles and descriptions) groups the fields of its row editor.

### Record layouts

`Meta.layout` can declare the whole record page instead of sections only: `Main(...)` first, then
blocks in order. Every block after the primary area is a row of "more information"
([Compact record page](#compact-record-page)):

```python
from evlet import Chart, Count, Html, Main, Model, Ref, Related


class Employee(Model):
    class Meta:
        layout = (
            Main(('full_name', 'email', 'department', 'reports_to'), 'skills'),   # fields, then a Table field
            Html('<p>Skills of <strong>{full_name}</strong> are kept with the record.</p>'),
            Chart('stat', y='people', model='employee', link='reports_to', label='Direct reports'),
            Chart('bar', x='department', y='people', model='employee', link='reports_to'),
            Related('employee', 'reports_to', label='Direct reports', fields=('full_name', 'email'), order='full_name'),
            Related('employee', 'reports_to', label='Same department', filters={'department': Ref('department')}),
        )
        measures = {'people': Count()}
```

| Block | What it shows |
|---|---|
| `Main(fields, *tables)` | The primary area, first: fields, then Table fields. |
| `Section(...)` or `('Title', fields)` | Fields, as before. Without `Main`, the first section is the primary area. |
| A `Table` field name | The child table, as a row of more information: its title, note and Focus and Columns tools on one line across the top and the table the full width of the card below (a section keeps its heading on the left). A Table no block places joins the primary area. |
| `Related(model, link=None, *, label, filters, fields, limit, order)` | Records of `model` whose Link `link` points to this record, `limit` a page (default 5, at most 20), with a pager and **View all**. |
| `Html(content)` | Text from the app. `{field}` shows that field of the record. |
| `Chart(type, x=None, y=..., *, model, link=None, filters, label)` | Declared measures of `model` over the records that point to this one: `'stat'` numbers, or `'bar'` or `'line'` grouped by `x` (`'status'`, `'due:month'`). |

- **No tabs.** `Tab`, `Meta.tabs` and `Meta.default_tab` were removed in 0.4. Using them fails when the
  model is defined, with a message pointing to `Main` and `Section`. Activity is in the side column;
  Revisions, Related and Postings are rows of more information.
- **Fields no section lists** show as Other, last, as before.
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
- **The automatic Related row** turns off for a model whose layout declares a `Related` block. Name
  `related` in `Meta.side_blocks` to keep it. A model without Related blocks keeps it, as before.
- **Permissions.** Related rows come from `GET /api/{model}` with the block's filters and
  `[link, "eq", <record id>]`, and charts from `GET /api/{model}/aggregate`, so row scopes, field
  permissions and filter checks apply as for any list. `/api/schema` leaves out a Related or Chart
  block the role cannot use (the model, its Link, a filter field or every measure unreadable) and
  cuts Related columns and order to readable fields.
- **Loading.** A block asks for its rows when it first scrolls into view, and a save of the
  record loads shown blocks again. A related record and a chart need a saved record, so a new
  record shows only its sections, tables and Html.
- **Side panel and phones.** A list's side panel shows the same rows, less what `Meta.panel` hides,
  and the side column opens from the drawer button.
- **Errors.** An unknown model, field, Link or Table name, a bad filter or operator, a `Ref` to a
  field the model does not show, an unknown measure or group field, or two sections with one title
  fails when the registry is built, naming the model and the block.

`/api/schema` sends a layout with `Main` or blocks besides sections as `record_layout`:
`{"blocks": [...]}`, each block with its `kind` (`section`, `table`, `related`, `html`, `chart`).
Sections are named by title and keep their fields in `layout`, which every other client reads as
before. A model whose layout has only sections, or no `Meta.layout`, has no `record_layout`: its first
section (the default one holds every field) is the primary area, with every Table after its fields.
The Playground's Employee page uses every block type.

### Compact record page

Every record page is compact: cards at the top, a primary area without a heading, and every block after
it as "more information". There are no tabs. A layout that starts with `Main` names the primary area;
without `Main` it is the first section, and a model without `Meta.layout` puts every field in it, in
declaration order, followed by its Tables.

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
  count reads as a plain number, never money. A sum of money whose code each record takes from its own
  fields (`currency=` naming a field or a Link) shows without a symbol, since no one code covers it. A
  stored field people fill in, a hidden field, a model-level measure, an unknown name, a name given
  twice, more than five, or a name that is both a field and a measure fails when the registry is
  built. Cards are for root models, not Table rows. `/api/schema` sends `cards` as a list of names,
  like `key_fields`, cut to what the role reads: a field it can read, a measure whose fields it can
  read. Without `Meta.cards` nothing changes and the summary strip works as before.
- **`Main(fields, *tables)`** is the primary area: a grid of fields, then Table fields, with no
  heading. Use it once, first. A layout with `Main` places every `Table` field (in `Main` or after it). Where a name is needed (`Meta.panel`, Controls, Form layout) the area is named by
  the model's label, so a section cannot share that title.
- **`Section(..., description=...)`** adds a line of text under a section's title. Any layout can use it.
- **More information.** Every `Section`, `Table`, `Related`, `Html` and `Chart` after the primary area
  shows as a row that opens and closes, with a one-line summary while it is closed.
- **Header and side column.** The header has the title, the status badge (the State with `show="badge"`, and
  `Meta.status`), a lock while the record can't be changed, and the actions; under the title a muted line with the
  first two key fields that have a value. A State with `show="bar"` draws the stage bar above the key cards on the
  full page and the badge in a side panel; `show="none"` draws neither.
  A model whose schema names a record image (`image`, a field of type `image`) shows it left of the title, a round
  avatar for a person and a square thumbnail otherwise, in place of the icon tile; without one, nothing shows. `Meta.key_fields` keep every other use (search, reference labels, lists, board cards,
  connectors); on a compact page their values show in the form itself, not in the header or the side column.

`/api/schema` sends `record_layout` as `{"blocks": [...]}` (the `compact` key of 0.3 is gone: every page
is compact). The Main fields are a
`section` block named by the model's label and the Main tables are `table` blocks, each with
`"main": true`; the section in `layout` has `"main": true` too, and a section with a description
has `"description"`. A client that ignores these keys shows the blocks in order, as it would any
record layout. The Playground's Project uses it.

The record page draws it on a record's full page and in a list's side panel (the same view, narrower there):

- **Key cards** for `Meta.cards` replace the summary strip: number cards above the white card, the same thin stat
  cards as a list's measures, each a field's label over its value, spaced as on the list page. A wide record puts
  them in one row. A narrow one (a side panel, a phone) puts them two to a row, three when there are three; an odd
  last card takes the whole row, and long values wrap rather than being cut short. A model without `Meta.cards`
  keeps the summary strip. `Meta.panel_summary = False` hides them in a side panel.
- **Locks show once.** A record its state locks, or one the role can't change, says so in its banner or with the
  lock in its header; its fields carry no lock of their own. A field that is read only while the record can still
  change keeps its "read only" or "locked" hint. The row editor of locked lines does the same.
- **One white card** holds the rest of the record: the state banner strip at its top, the Main area and more
  information, separated by thin lines.
- **The Main area** has the Main fields in a grid of four columns (three, two, then one as the record's own width narrows), edited in place, then its Tables: each with its title, a muted row count, Add on the right and two small buttons, Focus (the table takes the content area; Esc leaves it) and Columns, and a light dashed box
  while it has no rows. A row's Edit opens the row editor in focus view: the rows as a list beside one form for the
  row, which shows the row model's first section as a grid and its other sections as "more information" rows. A
  table that takes section rows groups its lines under them, each with its own sums ([ui-framework.md](ui-framework.md)).
- **More information** lists every later block as a row: the title, the section description and a chevron on the
  left, the content on the right. Closed, a Section shows its values on one line ("Label: value · Label: Not set · ...",
  cut with an ellipsis), a Table its row count and a Related block its record count. Sections start as
  `collapsed` says and the person's choice is kept, as for any Section. Related records the layout does not list,
  Revisions and Postings are rows here too.
- **The side column** has Attachments and Activity. Its note box has a slim toolbar: bold, italic, bulleted
  list, link and Mention. In a narrow window or a side panel it is a drawer behind a button, so the record's
  content always comes first. The record footer sits under the card.

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
  A tab the address names when the list opens stays there, the default one too, until another is
  chosen. Clear filters goes back to the default tab. A tab with no records and no search or filters
  offers Show all instead.
- **Controls.** The list view sets both for an instance ("Tabs by" and "Opens on",
  [controls.md](controls.md)).

The Playground's Projects list opens on Active, with tabs for Planned, Active and Done.

A record's State shows as a coloured badge on the title's line by default, with its moves on the
header's split button; `State(..., show="bar")` draws the stage bar instead, for pipelines, and `show="none"`
draws neither. `Meta.status` adds a status badge for a Select the system sets, such as a
payment status, coloured by `Select(..., colors={...})`. Badges and bar steps offer only the
moves this person may take now; everything else is plain text. Lists draw the same badges.
`State(..., cancelled=(...))` marks final states that mean the record was called off; the
schema's `state.cancelled` lists them, with `"void"` on a voidable State. The
badge wraps with the title in the side panel and on a phone; the bar scrolls sideways there.
See [states-actions.md](states-actions.md#how-a-state-and-a-status-show).

Related records (`Meta.relations`) show in the Related row of "more information": closed, it counts the
records of each relation; open, it has a short table per relation. Nothing is asked for when a
record opens: a relation's records load when the Related row first scrolls into view (at once in a
browser without IntersectionObserver). One server page of five records serves both the count and the
table, and it stays loaded while the record is open; a save of the record reloads the pages already
shown. Each section shows five rows; with more, a small pager under it reads "1–5 of 12" with previous and
next, each a server page (`limit` and `offset`). **View all** in the section's heading opens the
related model's list filtered to this record (`?f=[["customer","eq","<id>"]]`), shown as a
filter that can be removed. It is left out when the role cannot filter that list on the link.

## Workspaces

A workspace is a named sidebar: groups of items, three levels deep (workspace, group, item). An app
can declare none, one or several. `/api/schema` gives each role its own view of them under
`workspaces`, and the sidebar draws them ([below](#sidebar-and-workspaces)).

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
| `/`, `/login` | Sign-in, or the start page when signed in. `/login?user=NAME` fills in the username, and `Security(admin_sign_in=True)` fills in `administrator`. An account without a password yet sees "Set your password" first |
| `/docs`, `/docs/<topic>` | Docs |
| `/app/<model>` | List (and board, when `Meta.board` names the State). Search, sort, page, view and filters are in the query |
| `/app/<model>/new` | New record. Query values fill writable fields: `?customer=4` |
| `/app/<model>/<id>` | Record view |
| `/app/<model>/<id>/view` | Older record link; opens the record view |
| `/settings/<page>`, `/settings/app/<app>` | Settings ([settings.md](settings.md)); `/settings` opens the last page |
| `/settings/accounts` | Redirects to `/controls/users` |
| `/controls`, `/controls/<page>` | Controls ([controls.md](controls.md)), a separate page for roles that open it |
| `/eva`, `/eva/<chat>`, `/eva/tasks` | Eva's chat and scheduled tasks ([eva.md](eva.md)). Served only when config.py turns Eva on; the page asks `/api/_eva` and shows nothing of Eva to people without it |
| `/reports`, `/reports/<name>` | The reports this role may run, and one report's view ([reports.md](reports.md)) |
| `/?explorer=reports`, `data`, `dashboards` | The Explorer ([below](#explorer)) |

## Sidebar and workspaces

The sidebar has three levels: workspace, section, shortcut. It starts directly with its navigation
(Docs, Eva, then the sections); the brand and the Workspace switcher are in the top bar
([Top bar](#top-bar-search-and-explorer)).

- **Workspace switcher.** A compact button in the top bar, after the Evlet brand and a pipe: the
  current workspace's name in the normal text colour and the up-down chevrons (`chevrons-up-down`), in
  a flat box with a 1 px border and 4 px corners, as tall as the search box. Hover gives it a light grey
  background; while its menu is open the border is stronger too. Its tooltip and accessible name
  are "Workspace: Sales". A click opens a menu below the button, aligned to its left edge (moved left
  to stay on screen), as wide as the other menus, flat, with 4 px corners. It lists one row per
  workspace, with the workspace's icon and name; the current one is in the accent colour with a check.
  For people with access, a divider and **Customize** follow. Arrow Up and Arrow Down move through the
  rows (wrapping at either end), Home and End jump to the first and last, Enter opens one, and Esc
  closes the menu and gives focus back to the button. The menu is a `menu` of `menuitemradio` rows.
  Opening a workspace shows its sections and opens its first shortcut.
- **The workspace stays put.** Opening a page of another workspace (a link, Back, the search palette,
  a related record) keeps the current workspace and its sidebar; nothing is marked there, since no
  shortcut matches. The breadcrumb shows where the page belongs: its first crumb names the page's own
  workspace (the first declared one holding a shortcut to it), such as "Billing › Invoices › INV-…",
  as a link to the page. Clicking it shows that workspace, the page staying open. When the page's own
  first crumb has the workspace's name (a report's "Reports" in the Reports workspace, a record's
  "Projects" list in the Projects workspace), the two are one crumb: "Reports › Projects › Projects by
  stage". That crumb keeps the page crumb's link and opens it, then shows the workspace. The switcher and
  that crumb are the only things that change the workspace. The workspace is kept per browser tab in
  sessionStorage (`evlet.<username>.nav.workspace`), so a reload keeps it and a new tab does not. A tab
  without one (a new tab, a pasted link, a bookmark) opens on the page's own workspace; a page no
  workspace holds, such as the start page, opens on the person's last choice, kept on the device in
  localStorage under the same name. Blocked storage falls back to the page's workspace.
- **Sections** are collapsible titles with an icon, an optional count and a chevron. Each one is open
  until the person closes it. The closed ones are kept per person in browser storage
  (`evlet.<username>.nav.closed`).
  Blocked storage only means nothing is remembered.
- **Shortcuts** sit under their section on a thin guide line, each starting with its kind's icon
  ([Navigation icons](#navigation-icons)), small and muted, in place of a dot. The one the page
  opens exactly (a list with its view, tab and filters, or a report) gets a light blue tint across the whole
  row, with its label and icon in the primary blue at weight 600; its record count stays muted. Hover on
  the other rows is a light grey.
- **The sidebar** is white (the surface colour) with a border on its right; the main area beside it is a
  light grey (`--page-bg`), so its cards read as cards. Dark themes keep the same structure: the sidebar
  and top bar on the dark surface, the main area a little darker.
- **Section titles** show the section's icon, a bold label, the number of its shortcuts while it is
  closed, and a chevron (right when closed, down when open). **The section that holds that shortcut**
  has its icon, label and chevron in the primary blue, with no fill. It keeps them when closed, so the
  person still sees where they are.
- The collapsed sidebar shows each shortcut's kind icon with its name in a tooltip, and the active one blue
  on the same light blue tile; section titles give way to a thin rule. The round button on the sidebar's
  edge, just above the account card, collapses and expands it (and `[`). On phones the sidebar is the
  drawer, opened from the top bar's menu button.

**Where the sidebar comes from.** `workspaceNav()` in `app/workspaces.ts` maps the schema's
`workspaces` ([Workspaces](#workspaces)): each group is a section (with its icon, `layers` without
one) and each item a shortcut. A model item opens its list, with `view=board` for a board and its
filters in the list address: an equality on the field of the list tabs (`Meta.list_tabs`) opens that
tab, and the rest go in `?f=` in the form the list's filter rows write back (a Boolean equality is
"is yes" or "is no", a Select equality is "in"). A report item opens its page, a slip item presents the
slip over the page (`presentSlip`), and a URL item opens its address (an http(s) address in a new tab).
A shortcut opens exactly what it names. A model item without a tab of its own, on a list whose default
tab (`Meta.list_tab`) is not All, says `?tab=all`, so clicking "Projects" opens the plain list on All,
not on the default tab. Only a shortcut that declares `view` or `filters` applies them. The list keeps
no saved per-person view or filter to restore, so nothing else applies. Reached any other way (search,
a link, the start page, the breadcrumb), the list has no `?tab=` and opens on its default tab. Clicking
a shortcut to the list already on screen opens what the shortcut names there too.
Workspaces added in Controls (`source` custom) show like the others. The remembered open sections and
the chosen workspace use the workspace and group keys.

**The active shortcut** follows the address, by exact match. On a list, the path and the view, tab and
filters must be exactly the shortcut's, none more and none fewer; search, sort, page and the order
of the parts don't count. So `/app/task?f=[["done","no",null]]` marks "Open tasks" and
`/app/task` marks "Tasks", while another filter or tab marks neither. A tab the address names on
arrival stays in it, the default one too, until another tab is chosen, so a shortcut to the default
tab stays marked after its click. The list reached without `?tab=` (search, a link) shows its default
tab and marks no shortcut, nor its section. A record page (a record, a new record, a record's view)
marks none either, nor its section, and so does a record open in the list's side panel (`?open=`,
a new one included); closing the panel marks the list's shortcut again. On a tie, the shortcut of the workspace on screen wins, so a list two workspaces share
keeps the sidebar as it is. A URL shortcut is marked only on its own address, every query part
included: "All reports" at `/?explorer=reports` is not marked on another Explorer view or on the root.

An older server without `workspaces` gets the fallback: one workspace per installed app (the first
part of each model's `resource`, such as `showcase` in `showcase.Project`; models without one form a
workspace named after the application), with **Documents** (models with a State or a Table),
**Records** (the others) and **Reports** (the app's reports) sections. Both feed one shape:

```ts
{ workspaces: [{ key, label, icon, source?, sections: [{ key, label, icon, count?, items: [{ key, label, href, kind, icon, model?, slip?, external? }] }] }] }
```

### Customizing workspaces

Admins and roles with **Customize workspaces** see **Customize** at the bottom of the switcher menu. Nothing in the
session says who has it, so the first time the menu opens the browser asks
`GET /api/_controls/workspaces` once; the route answers everyone else 404 and nothing shows. Customize
opens a dialog on the current workspace (a picker switches to another):

- Drag a grip, or focus it and press Space then the arrow keys, to reorder groups, and to reorder
  items or move them to another group.
- The eye hides or shows a group or an item; a hidden one stays in the list, struck through.
- Each name is an input: renaming a workspace, group or item sends only names that differ from the
  declared ones. Each item shows its kind's icon; there is no icon to choose per item.
- **Add shortcut** in a group adds a list (with List or Board and optional filters as JSON), a
  report, a slip or an address, with an optional label (needed for an address). **Add group** adds a
  group, and **New workspace** starts a custom workspace whose key comes from its name.
- **Save** sends the whole override with `PUT /api/_controls/workspaces/<key>`, or resets when
  nothing differs from the declared workspace. The server checks everything again; a 422 shows each
  problem beside the row its path names (`groups.0.items.2.shortcut`). After a save the browser reads
  `/api/schema` again and the sidebar follows at once.
- **Reset to declared** (or **Remove workspace** for a custom one) asks first, then sends `DELETE`.

Hiding is never a permission: every page still checks access, and the schema filters items for
each role after the override. Changes need a recent two-step code where Controls asks for one.

### Navigation icons

A shortcut's icon comes from what it opens, never from the item, so every list looks alike and an
app has nothing to set. `app/nav-icons.ts` is the one map; the sidebar, the collapsed sidebar, the
workspace editor, the Explorer cards and the search palette all read it. Group and workspace icons
stay as declared. Every name is in the browser's icon set (`ui/icon-data.ts`); where the ideal icon
is missing, the closest one stands in (a slip uses `sticky-note`, a dashboard `activity`).

| Kind | Icon | Used for |
| --- | --- | --- |
| List | `list` | A model's list, filtered or not |
| Board | `square-kanban` | A model's board (`view='board'`) |
| Report | `chart-column` | A report, and Explorer report cards |
| Dashboard | `activity` | Dashboards, when there are any |
| Slip | `sticky-note` | A slip opened over the page |
| Link | `link` | An app path or an http(s) address |

| Shell element | Icon |
| --- | --- |
| Docs | `file-text` |
| Explorer | `layout-grid` |
| Search | `search` |
| History (recent records) | `history` |
| Notifications | `bell` |
| Help (shortcuts) | `circle-help` |
| Settings, Controls | `settings`, `sliders-horizontal` |
| Account | `user` |
| Workspace (its row in the switcher menu) | `layers` (when the workspace declares none) |
| Customize | `pencil` |
| Explorer views: Reports, Data, Dashboards, Ledger reports | `chart-column`, `table-2`, `activity`, `receipt` |

### Top bar: Search and Explorer

The top bar runs across the whole window, over the sidebar and the main column, on the white surface
with a light divider under it. From left to right it holds the drawer button (phones only), the **Evlet
brand**, a pipe and the **Workspace switcher**; then, on the right, the **search** button, the **history**
button, notifications and the shortcuts button. The breadcrumb is not in the top bar: it is a small line
above each page's title ([Breadcrumb](#breadcrumb)).

- **Brand.** The Evlet logo, the same artwork and size as on the sign-in page and before: the
  horizontal evlet.io lockup, 26 px tall (`evlet-logo-primary.svg`, and `evlet-logo-white.svg` in dark
  themes). The brand is shown here; the sidebar shows it only as a drawer on narrow windows.
- **Pipe.** A 1 px line, 16 px tall, in the strong border colour, with space on both sides.
- **Switcher.** The compact Workspace button ([Sidebar and workspaces](#sidebar-and-workspaces)).
- **Width.** With the sidebar expanded, the brand, pipe and switcher take the sidebar's width (a long
  workspace name is cut with an ellipsis). With the sidebar collapsed they take only the room they need:
  the Evlet mark alone (`evlet-icon-blue.svg`, 26 px), the pipe and the switcher.
- **Narrow windows** keep the main column at least about 600 px wide, in two steps. At 1220 px and
  under, the sidebar and the switcher leave the page: a menu button at the top bar's left opens them as a
  drawer over the page, with the brand at its top (the same lockup and size as the top bar), the
  switcher, then the navigation. Esc, a click outside or opening a page closes it. The collapsed rail
  is for wider windows only. A record whose own frame is under 960 px wide (about a 960 px window, or
  any side panel of default width) has no side column: a button at the end of the breadcrumb row (the
  `panel-right` icon) opens it as a drawer on the right. This step follows the record's width, not the window's.
- **Phones** (under 760 px): the menu button, then on the right the search icon, history and
  notifications. The page never scrolls sideways.

- **History** (`history` icon, tooltip "Recent records") sits on the right, after the search button and
  directly left of the notification bell. A click or Arrow Down opens this session's recent records, as
  before. On phones it stays visible.
- **Search** is one button in a box, with the search icon, the word "Search" and its key ("⌘K" on a
  Mac, "Ctrl+K" elsewhere; the tooltip reads "Search ⌘K" or "Search Ctrl+K"). It opens the search
  palette, as do ⌘K and Ctrl K. The top bar has no Explorer button.
- **Explorer, from the palette.** At the end of the palette's search field is an icon-only button with
  the app grid icon (`layout-grid`), named "Explorer", with the tooltip "Explorer G E" on hover and
  focus. Tab reaches it from the field. It is flat, with 4 px corners. A click closes the palette and
  opens the Explorer (`/?explorer=<view>`) on the view this person used last on this device
  (`evlet.<username>.explorer.view`; Reports the first time), as does G then E anywhere. When the field
  has text, the Explorer's search is filled with it (`?q=`), as with the entries below.
- **The palette reaches the Explorer too.** Typing "explorer" lists **Explorer** and **Explorer:
  Reports / Data / Dashboards** (and **Ledger reports** when there are any) under Pages. Any typed
  text also offers, last, **Search all reports for "…"** and **Search all data for "…"**, which open
  that view with its search box filled (`?q=`). They stay offered when nothing else matches.
- **Phones** (under 760 px) show only the search icon button; the Explorer button stays in the
  palette's field.
- **One mark at a time.** The sidebar has no Explorer button, and a URL shortcut is marked only on its
  own address, so the Explorer page never shows two active items.

### Breadcrumb

The breadcrumb is a small muted line (12.5 px) in the main area, directly above the page's title and
aligned with it: "Sales / Invoices", the crumbs joined by slashes, the page itself last. List pages,
record pages, reports and the Reports page, Docs, the Explorer, Settings and Eva's scheduled tasks
place it there (`ctx.crumbBar()`); a record in a list's side panel has none, the list above it has
the one. It scrolls away with the title.

- The first crumb names the workspace the page belongs to (the first declared one holding it).
  Clicking it shows that workspace in the sidebar, the page staying open. When the page's next crumb
  has the workspace's name, the two are one crumb.
- A record's last crumb is its document number when the model has one ("Projects / PRJ-2026-0021"),
  else its title. The last crumb is darker than the others.
- Phones keep the last two crumbs; a long last crumb is cut with an ellipsis.

### Explorer

**Explorer**, from the button in the search palette's field, opens a page with three views (four with ledger reports):

- **Reports**: a card for every report the person may run, from every app, with its name, app and
  description (when the server sends one).
- **Ledger reports**: the reports over a ledger model (`source_kind` "ledger"), in the same cards.
  The view shows only when the person may run at least one; an old link to it opens Reports.
- **Data**: a card for every model the person can read, with its label, app and description. Child
  tables are left out. Ledger models have no public surface, so the schema never lists them. A card
  shows a record count only when the person turned sidebar counts on in Settings; the Explorer never
  asks for counts of its own.
- **Dashboards**: "No dashboards yet". The framework has no dashboards.

Reports, Ledger reports and Data have a search box (name, description and app) and an app filter. A card opens the
report or the list. The address is `/?explorer=<view>`: the root path is the one desk path the server
always serves, so the Explorer needs no server route, and a link or a refresh opens the same view.
The search palette lists it and each of its views under Pages, and opens it with a search filled
in. Report cards use the report icon and Data cards the list icon ([Navigation icons](#navigation-icons)).

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
An `Image` field ([model-engine.md](model-engine.md#image-fields)) shows a small preview with
Upload (Replace once set) and Remove. The browser shrinks a large image and checks its type before
upload; the server checks both again. A new record's image is held until the record is saved.
List cells read "Image". `Meta.image` puts the model's `image` key in `/api/schema`, which the record
header (page and side panel) shows left of the title: a round avatar for a person, else a square
thumbnail, served by the attachments route. The Playground's items have a Photo.
A record's Activity, in the side column, writes notes with the same editor; typing `@` mentions a person, with a
warning when they can't open the record. The bell in the top bar opens the notification centre,
with the unread count kept live by the event stream ([journal.md](journal.md#mentions)). It is a
panel along the right edge under the top bar, grouped into Today and Earlier; a click anywhere else,
Esc or the bell closes it. A click on an item marks it read. Go to shows only when the person can
open the item's record, and opens it. While the panel is closed, new items slide in as popups at the
top right for a few seconds (longer while pointed at or focused); a click on one opens the panel.
Today the inbox holds mentions; other kinds show with a bell icon.

### Collapsible sections

Every section after the primary area is a "more information" row that opens and closes from its
title: a real button with `aria-expanded` and `aria-controls`, so Enter and Space toggle it, and Tab
skips the fields of a closed row. A closed row shows its values on one line, and a badge with the number
of fields that have errors. The primary area never closes. (`FoldSection`, with "3 of 5 filled", remains
in the component library for forms of your own.)

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
- **Same everywhere.** New, read-only and locked records fold the same way, on the page and in the side
  panel. Print and PDF show everything: the browser's print style opens closed
  sections, and the server's print designs never use the form layout.
- Height animates only when the device does not ask for reduced motion.

### Field and tab order

The DOM order of a form is its visual order, and nothing uses a positive `tabindex`. A section with
`flow="columns"` lists its fields down the left column, then the right, so Tab goes the same way.
Tab skips values that can't change (read only, locked or calculated); a click still focuses them,
showing why, and screen readers read them. A record page has no tabs.

### Focus on new forms

A new record (the full page `/app/<model>/new` and the side panel) and a slip focus their first
editable field when they open, so typing can start at once. The field is the first one in screen
order that shows and can be edited: read-only, calculated, hidden and locked fields are skipped, and
so are the fields of a closed row. The primary area comes first, then the sections in the order the
layout shows them. The rules:

- An existing record never takes the focus on open, so keys and scrolling stay where they were.
- Touch devices (no hover, or a coarse pointer) skip it, so the on-screen keyboard does not open.
  Dialogs there focus the dialog itself rather than its first field, for the same reason.
- A focus the person moved while the form loaded stays where it is.
- Restoring an unsaved draft, or starting fresh, focuses the first field again; the values do not
  change.

The choice goes through `initialFocusField(model, layout)` in `ui/autofocus.ts`. A model's
`Meta.focus` (or a slip's `focus`) wins when that field shows and can be edited in an open section;
otherwise the first such field gets the focus, as above.

A focused field shows a solid 1 px accent border, with no glow; a field with an error keeps its rose
border when focused. Buttons and other controls show a solid 2 px accent outline.

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

### Keyboard shortcuts

Lists, record forms and dialogs follow the keys people know from other apps. Mod is ⌘ on a Mac and
Ctrl on Windows and Linux; the help sheet, tooltips and menus write each key the way the platform
does (⌘S and ⇧⌘D on a Mac, Ctrl+S and Ctrl+Shift+D elsewhere).

| Keys | Where | What it does |
|---|---|---|
| Mod+S | Record form, new or saved | Save, as the Save (or Create) button does: the same checks, errors and jump to the first error. With nothing changed it does nothing. It works while typing in a field or the rich text editor, and never opens the browser's Save Page |
| Mod+S | Dialog | The main action: Confirm, Save, Run, Hand in for a slip |
| Mod+Shift+D | Saved record | Delete: opens the usual confirmation. Only where the record's More menu has Delete (the role may delete, the record is not locked or posted) |
| Mod+Shift+D | New record | Discard it and go back to the list, asking first when something was typed |
| Mod+Shift+D | Dialog | Cancel, as Esc does |
| Alt+N (⌥N) | List and record page | A new record of the model, on its page or in the side panel as the list's New does |
| Alt+Shift+D (⌥⇧D) | Saved record | [Duplicate](#duplicate): a new, unsaved copy of the record, where the record is. Only where the More menu has Duplicate; nothing on a new record |
| C | List | A new record (kept from before) |
| / | List | Focus the list's search |
| Enter | List row | Open the focused row |
| ← → ↑ ↓ | Dialog actions | Move between the footer's buttons, wrapping; Enter presses the focused one |
| ? | Anywhere | The shortcut sheet: this screen's keys, then those that work everywhere and in a dialog |

- **New is Alt+N, not Ctrl+N.** Browsers keep Ctrl+N and ⌘N for a new window, so a page can't use them.
  Alt+N is read from the physical key, because Option+N on a Mac types an accent.
- **Duplicate is Alt+Shift+D.** Like Alt+N it is read from the physical key, because Option+Shift+D
  on a Mac types a character.
- **Plain keys stay out of the way.** C, / and ? never fire while typing in a field. Mod+S does.
- **One screen at a time.** A shortcut acts on the screen that holds the focus. With a record open in a
  side panel, the keys act on that record while the focus is inside the panel, and on the list
  otherwise (Alt+N from the panel opens the new record in the panel). Nothing on the screen fires while
  a dialog is open: the dialog answers Mod+S and Mod+Shift+D itself.
- **A held key acts once.** Key repeat never saves, deletes, duplicates or opens a second record.
- **Dialogs.** A dialog without a field (a confirm, the unsaved-changes prompt) focuses its main
  action, so Enter answers it. A dialog that asks for input (an action's inputs, a move's values, a
  slip) focuses its first field, and Mod+S submits it. Focus stays in the dialog and goes back to
  what opened it.
- **Delete always asks.** Mod+Shift+D only opens the confirmation, with Delete focused. Deleting takes
  a second, separate key: Enter or Mod+S. An arrow key moves to Cancel.

### Ids, names and titles

Record addresses use the record's text id, `/app/invoice/01hc1sd9vc1ye`, so changing a name never
breaks a link or a bookmark. An older bookmark with a decimal id opens the same record and the
address changes to its text id. A record page shows the record's `title` as its heading and, when
the model has naming, its `name` next to it in a monospaced font, or "Assigned on save" or
"Assigned on confirm" until it has one. Link pickers, list rows, search and the recent records
show titles; the schema's `title_field` is `title` for every record model
([model-engine.md](model-engine.md#name-and-title)).

### Created and last modified

The footer of every record page shows when the record was created and last changed, and
by whom: `Created Aslam · 2 hours ago`. Hover or focus a time for the exact time in your zone
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

### List card

The page's title, its stat cards and the breadcrumb sit on the grey main area. Under them, the list's
tabs, its search row and its table are one white card, with a 1 px border and 4 px corners. Scrolled
up, the card sticks 12 px below the top of the main area and fills the visible height down to 16 px
from the bottom: the title, summary and stat cards scroll away, and the card's tab row, search row
and the table's header row stay in view. Only the table's body scrolls inside the card, down and
sideways; the page never scrolls sideways. The pinned columns, column resizing, the pager (at the foot
of the card) and the bulk bar work as before. The loading lines and the empty states show inside the
card. The board sits in the same card, which keeps the board's own height and does not stick.
On a phone the card scrolls with the page and the table is as tall as its rows.

A record opened in the list's side panel is its own white card beside the list card, about 13 px
apart, with its own border and 4 px corners; it is not a drawer against the window's edge. Both cards
fill the visible height and each scrolls inside: the table's body in the list card, the record in its
card, whose header (the title, its status and Expand and Close on one row) is at its top. The
divider between the cards still resizes the panel. When the panel covers the list (a narrow window,
a phone) it fills the main area as before. The docked filter panel is a card the same way.

While rows load (the first load, a tab, a search), a list with no rows to show yet shows loading
lines, and the board shows them until its first load. The empty state ("No projects in this tab",
"No projects yet") shows only after a load found nothing.

### Filters

A list's Filter button (with a count of active filters) opens the filter side panel, docked
beside the list inside the page, like the record side panel. There is no overlay and no
backdrop: the list narrows and stays usable, and the panel fills the height to the bottom of the
screen. On a phone it is a full-width sheet in the same place. The filter panel and a record's side
panel are never open together. Opening a record in the side panel closes the filter panel; the
filters stay applied and the Filter button keeps their count. Closing that record (Close, Escape or
Back) opens the filter panel again, without moving focus. Filter, while a record is open in the
side panel, closes the record first, so unsaved changes ask as when leaving it (see Unsaved
changes); staying on the record keeps the filter panel closed. The panel has two halves of about
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
