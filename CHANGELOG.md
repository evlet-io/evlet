# Changelog

Evlet is pre-1.0 and not production ready. From 0.3, a public name or argument that is
removed or changed keeps working with a `DeprecationWarning` for at least one minor release
first ([docs/api-stability.md](docs/api-stability.md)). The notes below say what to change.

## Unreleased

### Added

- **`self.context.session` in run methods and actions.** A Move's `run` method and an `@action`
  handler can write other rows, such as ledger rows, in the move or action's own transaction:
  `self.context.session` is its `evlet.Session`, with `tx.lock`, `tx.insert_many`,
  `tx.reserve_ids` and `tx.aggregate`. The State change, the Sequence number, the record's changes
  and those rows commit together, and any failure rolls all of it back. It is trusted code that
  bypasses row and field permissions. It is open only while the handler runs; every other context
  refuses it with `AccessError` (403), including services, reports and everything Eva, assistants,
  tools, MCP and the connector reach. Before, this needed a legacy `Action` and `tx.maintenance()`.
  The Playground's Recognise action on an employee writes its Recognition ledger this way
  ([docs/states-actions.md](docs/states-actions.md#the-transaction-session-in-run-methods-and-actions)).
- **`evlet setup` in one go.** `evlet setup myproject --app notes,crm --instance acme --dev` creates
  the project, apps and instance, installs the apps in dependency order and starts `evlet dev` on
  loopback. `--get URL,URL` fetches apps from Git with the `app --get` checks and trust question;
  `--app` and `--get` take comma lists and combine. `--trust` answers the trust questions for the
  whole run. `--host erp.example.com` instead asks for the first administrator in the terminal,
  sets the instance domain, writes a Caddy site and a systemd unit under `deploy/`, checks DNS and
  prints the `sudo` steps; `--apply` runs them, showing each first. `--dev` and `--host` cannot be
  combined. A failed step is named, and running the same command again skips finished steps.
  Without the new options `evlet setup` is unchanged ([docs/cli.md](docs/cli.md#setup-in-one-go)).
- **Composite and unique indexes on normal models.** `Meta.indexes` now works on every model, with
  the ledger form: `[(("station", "on_date", "kind"), {"unique": True}), ("on_date",)]`. The
  database enforces a unique entry on SQLite and PostgreSQL, and a write that breaks it raises
  `ValidationError` on the entry's fields (HTTP 422). A `Child` row can name its owner field for a
  value unique per record. The entries are part of the saved schema and the startup check, and
  `AddIndex(table, ("a", "b"), unique=True)` adds one. Models without `Meta.indexes` are unchanged
  ([docs/model-engine.md](docs/model-engine.md#composite-and-unique-indexes)).
- **Paged aggregates and long `in` lists for trusted batch code.** `tx.aggregate(...)` and
  `database.aggregate(...)` take `limit` (1 to 10,000) and `after` (the previous page's last
  group) to walk any number of groups in a stable order, each exactly once, a page at a time.
  Trusted session queries (`tx.list`, `tx.count`, `tx.aggregate` and the `database` forms)
  take `in` and `nin` lists of up to 30,000 values in all, each bound as a parameter. Jobs that
  chunked lists to 100 values and groupings to 500 groups can drop the chunking. HTTP,
  `Context`, Eva, tools, MCP and Connector keep their limits of 100 values and 500 groups
  ([docs/measures.md](docs/measures.md#paging-through-many-groups),
  [docs/model-engine.md](docs/model-engine.md#long-in-lists-in-trusted-code)).
- **`tx.aggregate(...)`.** The transaction session runs the same exact aggregates as
  `database.aggregate`, with the same arguments, limits and result, and sees the transaction's own
  writes. Use it instead of the internal `evlet.measures.aggregate(tx, ...)`
  ([docs/measures.md](docs/measures.md#ad-hoc-aggregates-in-python)).
- **Ledger ids.** A ledger `Meta.indexes` entry may name `id`, such as `("serial", "id")`.
  `tx.reserve_ids(model, count)` reserves ids so the rows of one `insert_many` batch can link to
  each other or to themselves; `insert_many` and `tx.create` take a reserved `id`. On SQLite a
  rollback hands the ids back; on PostgreSQL they come from the identity sequence and a rollback
  leaves a gap ([docs/ledger.md](docs/ledger.md#reserving-ids-txreserve_ids)).
- `Naming(..., label="Project number")` labels the standard `name` field for the model's domain on
  the form, in list columns, filters, search and the schema
  ([docs/sequences.md](docs/sequences.md)). The Playground labels it "Invoice number".

### Fixed

- A raw `UPDATE` or `DELETE` that an append-only ledger table refuses raises `ConflictError` with
  the message `<table> is append only: its rows are never changed or deleted` on SQLite and
  PostgreSQL. PostgreSQL raised a generic database error before. New PostgreSQL triggers raise an
  integrity error; ones made by 0.3 are recognised too, so no migration is needed.

- Records with text ids show their move and action buttons again. The browser client now reads
  record details from paths with a text id, so allowed moves are no longer refused.
- Record pages with a text id, such as `/app/<model>/01hff1w8vgqwf`, are served with 200 instead
  of 404. A decimal id from an older link is still served ([docs/routing.md](docs/routing.md)).
- More > Reset layout puts collapsible sections back to their starting state at once, including
  `collapsed="empty"`, instead of waiting for an animation that a background page never plays.
- **Back to the app from Controls.** The top of the Controls sidebar links back to the last app
  page, or the start page; the search palette offers Go to Controls to roles that open it.
- **Change password from the account menu.** It opens the Sign-in & security password form
  in a small dialog; both use one shared form.
- **Saved filters on lists.** The filter panel keeps the current filters under a name, per
  person on the device. Choosing one applies it; each can be deleted.
- **Files can be removed from a record.** Each file in the Files block has a remove button,
  shown to roles that can upload. It asks first, and a locked record shows none.
- **A read-only child table stays read only when opened in the row editor.** On a locked or
  cancelled record, or without write access, a row opened from the table or its focus view showed
  editable inputs. They are now read only, like the cells, and the rows still open to read.
- **List tabs by a chosen field, with a default tab.** `Meta.list_tabs` names the State, Select
  or Link field whose values are the list's tabs, and `Meta.list_tab` the tab the list opens on
  (`"all"` by default). Without them a model's State drives the tabs as before; `list_tabs = None`
  turns them off. Counts come from the new `GET /api/{model}/list_tabs`, which follows the list's
  filters, search and row scope; a Link shows its 8 most used values. `/api/schema` adds
  `list_tabs` for roles that can read the field (additive). Choosing a tab, All included, keeps it
  in the address. The Controls list view sets both per instance
  ([docs/ui.md](docs/ui.md#list-tabs)).
- **Ledger models.** `Meta.ledger = True` stores a model lean: an integer id the database assigns in
  insert order and the declared columns only, with no public surface (`Meta.api` is `None`). Options:
  `append_only` (database triggers refuse UPDATE and DELETE), `journal`, `timestamps` (`created_at`,
  `modified_at`), `indexes` (composite and unique) and `period`. `tx.insert_many(model, rows)` inserts
  rows with one prepared statement and returns their ids. New migration `SetAppendOnly`; `AddIndex`
  takes a tuple of fields for a ledger index. `evlet ledger list --instance NAME` prints each ledger
  model with its row count and options ([docs/ledger.md](docs/ledger.md)).
- **Transaction locks.** `tx.lock(*keys, timeout=None)` holds named locks on business keys until the
  transaction ends, PostgreSQL advisory locks taken in a fixed order, a key check on SQLite. A wait
  past `timeout` raises the new `LockTimeout`. Nothing changes for existing apps.
- **Progress for long jobs.** A job handler reports `context.progress(done, total, message)`.
  `Task(..., resumable=True)` runs a long rebuild or recalculation in steps, each in its own
  transaction; a step returns a checkpoint, and a job that stops (worker stopped, failed step or
  crashed process) resumes from its last committed checkpoint. Controls > Jobs shows progress,
  `GET /api/_jobs/<id>` returns it, and the new `evlet jobs list --instance NAME` prints it. The
  table `evlet_job_progress` is created when the queue starts; no migration is needed
  ([docs/jobs-realtime.md](docs/jobs-realtime.md#progress-and-long-jobs)).
- **The list board is opt-in.** A model's list offers the Board view only when `Meta.board`
  names its State field, such as `board = "stage"`, and the board groups by that field. Without
  it the list is a table with the saved views per state, and an old `?view=board` link opens the
  table. Apps that relied on the automatic board for every model with a State must now set
  `Meta.board`. `Meta.board` on any other field fails registration naming the model.

### Changed

- **Controls, Export as code now writes code that works.** It used to export form layout and
  list overrides as `FormView` and `ListView` code, which the browser ignored, so pasting it
  changed nothing. It now writes `Meta` options on the model's class in its models file:
  `layout = (Section(...), ...)` with `tabs` and `default_tab`, and `list_fields`, `open_in`,
  `list_tabs` and `list_tab`. Pasted into the model, they give the same screens as the
  overrides. Field widths follow the field type, and a list's page size and sort stay instance
  overrides; the export says so in a comment.
- **The list filter panel is a side panel.** It docks beside the table inside the page, with
  no overlay or blur, and fills the height of the screen. Filter rows (field, condition, value)
  sit on top and saved filters below; each half scrolls on its own. Escape closes it.
- **Related records load when shown, five at a time.** Opening a record no longer asks for
  every relation: the Related block and each Related tab section load when they scroll into
  view, and one page serves both. Each section shows five rows with a small pager, and View all
  opens the related list filtered to this record.
- **Lists run flush to the main area.** A model list's table no longer sits in a rounded,
  bordered card. Its rows run edge to edge, like a child table in focus view, and the list page
  uses the full width of the main area. Related lists and reports keep their own look.
- **`Meta.summary` and `Meta.side_blocks`.** `summary = False` leaves the record page's summary
  strip out. `side_blocks = ('related', 'files')` picks and orders the side blocks, from
  `key_fields`, `related`, `files` and `info`. Both are checked when the registry is built and go
  into the UI schema only when a model sets them.
- **More > Copy record ID** on the record page.
- **Choose what a list's side panel shows.** `panel=False` on a field, `Section` or `Table` keeps
  it on the full page only. `Meta.panel = ('Contact', 'notes', 'activity')` names the only parts
  the panel keeps, in order: sections, fields, tabs, `summary` and side blocks. Names are checked
  when the registry is built. Controls, Form layout has a panel button per section, field and tab,
  and a Side panel card for the rest, stored with the layout. Hiding is display only. A required
  field that is empty on a new record always shows, and the panel offers Open full page when it
  hides anything. The UI schema carries `panel` only when something is hidden.
- **The record page shows each fact once.** Key fields show in the header facts and no longer in
  the summary strip, which now holds calculated values and per-record measures the header doesn't
  show (no strip when nothing is left). The Key fields side block shows only while a tab other
  than Details is open. A saved record's Details form leaves out the title, name and State that the
  header shows; the title is edited in the header. A new record's form keeps them.
- **The Related side block lists only relations with records** and is left out when none has any.
  The Related tab still lists every relation.
- **Record info no longer shows the record id.** Use More > Copy record ID. The block is left out
  while it has nothing to show, such as an older record without times.

- **Related counts use the model's words.** In the side block, a relation named after its model
  is announced as "Payments, 1 payment" by screen readers; other relations still say "records".
  An empty relation in the Related tab is one short line instead of a table of headers, and its
  heading shows no 0.
- **Record tabs show their keyboard focus ring inside the tab**, so the scrolling tab strip no
  longer clips it.
- **The note composer shows one focus ring.** The editor inside it no longer draws its own
  border and ring, and the ring matches rich text form fields.
- **Controls Jobs lists the jobs on the left.** All sits on top, and each job shows its latest
  run and how many recent runs it has. A click (or the arrow keys) shows that job's schedules and
  runs on the right, and the choice stays in the address as `?job=<name>`. On a phone the jobs
  are a row of chips above the runs.
- **The bell opens a notification centre.** It is a panel along the right edge under the top
  bar, grouped by day, that closes on a click anywhere else or Esc. A click on an item marks it
  read; Go to opens its record when the person can open it. New items slide in as popups at the
  top right while the panel is closed.
- **Only the Controls nav list scrolls.** The brand and workspace at the top of the Controls
  sidebar and the account card at the bottom stay in place, like the app sidebar, also in the
  phone drawer.
- **Recent records moved from the sidebar to a history button left of search.** It lists them
  newest first with the model and when each was opened, and has a Clear action.

### Upgrade notes

- **Python pages are gone.** If an app imports `evlet.ui` page names (`PageView`, `ListView`,
  `FormView`, `Column`, `ViewRegistry` and the rest), delete those definitions and shape the
  generated screens with `Model.Meta` (`layout`, `list_fields`, `tabs`, `open_in`, `panel`). An
  `AppDefinition` may still pass `views=()`, as older app scaffolds do; a non-empty `views` or a
  `ui` registry is refused at start-up with a message that says so. `Application` and `App` no
  longer take `views` or `ui`. Clients that called `/api/ui` read `/api/schema` instead
  ([docs/app-upgrades.md](docs/app-upgrades.md#from-03-to-04)).
- **Native panels are gone.** Remove `.ts` and `.js` modules from an app's `views/` folder and
  the matching `package-data` line from its `pyproject.toml`. A build no longer runs Node for them.
- **`evlet new` is gone.** Use `evlet setup myproject --app notes --instance acme --dev`.
- **`evlet.metadata` is gone.** `field_schema`, `label_for`, `ui_schema` and `view_for` live in
  `evlet.ui.metadata`, which is internal ([docs/api-stability.md](docs/api-stability.md#internal)).

### Removed

These were never rendered or called at run time. Nothing an app or a person uses in the browser
changes.

- The Python page API in `evlet.ui` (definitions, compiler, component contracts, view registry
  and page expressions), and the `GET /api/ui` and `GET /api/ui/resolve` routes. Python page
  routes no longer claim paths in the route table. The Playground's Projects demo and Layouts
  pages, which were Python pages, are removed with it.
- Native TS/JS panels: `evlet/native.py`, `native_panels` in `/api/schema`, and
  `/assets/apps/...`.
- The `evlet new` standalone starter (`evlet/cli/legacy.py`). The nested app layout and
  `evlet app <name> --legacy` stay; apps such as Finance use that layout.
- The `evlet.metadata` compatibility module.
- `evlet.calc.Document.observe`, `observe_table`, `observe_rules`, `subscriptions`, `explain`
  and `dispose`. The server settles a document once and reads its snapshot; the browser engine
  keeps all of them.

## 0.3.0 (05-11-2026)

This release adds rich text with images and mentions, unsaved changes that survive leaving a
record, record names, titles and times, period locks, `Meta.api`, reports and print designs, and
framework migrations through `evlet migrate`. It covers #66 to #118. Evlet is still not
production ready. The calendar engine, notifications and approval workflows are planned for 0.4.0.

### Upgrade notes

- **Run `evlet migrate` for each instance.** An instance migrated with 0.2.0 refuses to serve
  until you do. It backs up the database, then runs the framework migrations in order:
  `0001_startup_tables`, `0002_tool_calls`, `0003_record_names`, `0004_record_times` and
  `0005_unsaved_changes`. Stop the server first. Migrations never run from the browser
  ([docs/app-upgrades.md](docs/app-upgrades.md#upgrade-evlet)).
- **Apps must allow Evlet 0.3.** Change an app's dependency from `evlet>=0.2.0,<0.3` to
  `evlet>=0.3.0,<0.4`. New apps get this by default.
- **Record ids are text outside the database.** Clients read ids as 13-character strings and
  build URLs from them; a decimal id in an API path is now a 404. Existing rows keep their ids.
- **Every record has `name` and `title`.** An app model that declares its own `name` field is
  refused; rename it in an app migration before `evlet migrate` (see Changed).
- **`State` now shows as a badge by default.** A record's state is a coloured badge in the
  header, with its moves as buttons. Add `show="bar"` to a State that is a real pipeline (draft,
  submitted, approved) to keep the stage bar, or `show="none"` to hide it.
- **`Meta.api`.** Nothing changes for existing models (`"full"` is the default). Use `"read"` for
  a model that only trusted Python may write, such as a ledger, and `None` for a model callers
  must not reach. A Link from a reachable model to an `api = None` model must be `hidden=True`.
- **Period locks.** Opt in with `Meta.period = "<date field>"`. Once an admin sets a lock date
  in Controls, Periods, records dated on or before it refuse every write, from HTTP and trusted
  Python alike. Data migrations and repairs that must write there use
  `session.unlocked_periods(reason)`, which is logged and audited.
- **`evlet dev` creates the first admin.** On an instance with no accounts it creates an active
  admin with a random password and prints a one-time sign-in link. Pass `--no-link` to skip it.
  `evlet serve`, tenants and deployments never create accounts. Use
  `evlet users set-password <instance> <username>` to set a real password.
- **Rich text is sanitised on the server.** Every `RichText` write keeps only the allowed tags
  and attributes; scripts, event handlers, `style`, `id`, other classes and `javascript:` or `data:`
  URLs are removed, and links keep only `http`, `https` and `mailto`. `max_length` (20,000 by
  default) counts the plain text. Code that writes HTML from Python gets the sanitised form back.
- **Unsaved changes have limits.** Edits left on a saved record are kept on the server for 30
  days, 200 entries per person, and 256 KB per entry; unsaved new records stay in the browser
  for 7 days, 20 per person. A larger entry stays only in the open tab. Admins change these in
  Controls (`unsaved.*`). Set `Meta.autosave = False` on a model that should keep the leave
  prompt instead.
- **Smaller changes that can break a client or an app.** A report filter field named `group`,
  `model`, `sortable`, `chart`, `export` or `group_by` must be renamed. Link filter errors have
  new text (still a 422 field error). In `/api/schema` a Currency field's code field moved from
  `currency` to `currency_source`. Filters take only the operators of their field kind, up to 50
  conditions, and filtering on a field the role cannot read is a 403.

### Added


- **`Meta.status` and `Select(colors=...)`.** `Meta.status = "<field>"` names a visible Select or
  State shown as the record's status badge and as a badge in lists, for a status the system sets
  (never a button). `colors` maps choices to `blue`, `green`, `amber`, `rose`, `violet`, `teal` or
  `slate`. A legacy string formula may now calculate a Select. The schema adds `status` on the model
  (for roles that can read the field) and `colors` on the field. The Playground invoice shows a
  calculated payment status (Unpaid, Partly paid, Paid) from a new `amount_paid` field; migration
  `0012_invoice_payment_status` adds both columns. Because the invoice now has a calculated field,
  an update to a Playground invoice sends `If-Match` with its ETag, as every calculated document does.
  Badge colours are display only: changing `colors` needs no migration.
- **Unsaved changes are kept when you leave a record, and the record is never written.** Leaving
  a saved record with unsaved edits keeps the edited fields in the new framework table
  `evlet_unsaved` (framework migration `0005_unsaved_changes`, so run `evlet migrate`), and the
  next visit offers Restore or Discard, with a per-field choice when the record changed in the
  meantime. Unsaved new records stay in the browser. Only Save writes the record, so no validator,
  event, number or history runs on leave. New routes `GET /api/_unsaved` and
  `GET/PUT/DELETE /api/_unsaved/<model>/<id>` (signed-in sessions only, own entries only), and
  Controls `GET /api/_controls/unsaved` and `POST /api/_controls/unsaved/clear` (counts only).
  New instance settings `unsaved.server_days`, `unsaved.server_entries`, `unsaved.browser_days`,
  `unsaved.browser_entries` and `unsaved.entry_kb`, the framework schedule `evlet_unsaved_purge`,
  the personal setting `keep_unsaved`, and `Meta.autosave` (`True` by default; `False` keeps the
  leave prompt), which `/api/schema` shows as `autosave`. What to change: nothing, unless a model
  should keep the leave prompt; then set `Meta.autosave = False`. Database tools that list
  framework tables see one more ([docs/ui.md](docs/ui.md#unsaved-changes)).
- **Mentions in the activity composer, and a Mentions inbox.** Notes on a record are written with
  the rich text editor; `@` opens a picker of active people, warning when someone can't open the
  record. Journal `note` and `message` entries take `html` (sanitised, mentions kept) beside `text`.
  Mentioned people get a row in the new per-user inbox (`evlet_inbox`, `/api/_inbox`), shown under
  the bell in the top bar with a live unread count, and an email when their notification preferences
  ask for one. Someone who can't open the record learns only that they were mentioned
  ([docs/journal.md](docs/journal.md#mentions)).
- **Images in rich text.** Paste, drop or upload a PNG, JPEG, GIF or WebP image straight into a
  `RichText` field; SVG is refused. The browser shrinks it to 2,000 px and re-encodes it before
  upload. Images are private attachments of the record, stored in the HTML by id only and served
  through the record's permission-checked download route; an unsaved record's images are held for
  the person for a day and linked on save, and removing an image from the text deletes it on save.
  New Controls settings `files.image_max_kb` and `files.images_per_record` set their limits apart
  from file attachments. Print Design embeds them and record emails send them inline
  ([docs/business-capabilities.md](docs/business-capabilities.md#images-in-rich-text)).
- **`RichText` fields and Evlet's own rich text editor.** `RichText(max_length=20000)` stores
  formatted text as an HTML subset: paragraphs, headings 2 and 3, bulleted, numbered and check lists
  nested up to three levels, quotes, code blocks, dividers, bold, italic, underline, strike, inline
  code, line breaks and http, https and mailto links. The server sanitises every write with a
  standard-library allowlist parser (`evlet/richtext.py`) and stores the plain text beside it in
  `<name>_text` for search, filters (`contains`, `icontains`, `nicontains`, `isnull`) and list cells.
  The browser edits it in a new editor with no dependencies (`frontend/src/evlet/richtext/`):
  `beforeinput` handling, IME and phone keyboards, its own undo, Markdown-style shortcuts, cleaned
  paste from Word, Outlook, Google Docs and web pages, a keyboard-reachable toolbar, a floating
  selection toolbar and a phone toolbar above the keyboard. Print Design prints it formatted. The
  Playground's projects gain a Brief (migration `0011_project_brief`). Nothing to change for
  existing models ([docs/model-engine.md](docs/model-engine.md),
  [docs/ui-framework.md](docs/ui-framework.md#rich-text)).
- **Field order, record tabs and Enter to move on.** `Section(..., flow="columns")` lists a
  section's fields down the left column, then the right (full-width fields break the run); the DOM
  and Tab follow the same order. `Meta.tabs` and `Meta.default_tab` order the record page's tabs and
  choose the one it opens on, validated at registration. The schema adds `flow` per layout entry and
  `tabs` and `default_tab` per model (additive). The Controls form layout gets Fields go, drag to
  reorder fields and sections (mouse, touch and keyboard, with announcements) and a Record tabs card;
  a layout with a tab order is saved as `{"sections", "tabs", "default_tab"}`, and plain lists load as
  before. Tab skips read-only and calculated values. A new Appearance setting, "Enter moves to the
  next field" (`enter_next`, off by default), speeds up data entry. The Playground's Customer uses
  `flow="columns"` and Project sets `Meta.tabs`.
- **Collapsible form sections.** `evlet.Section(title, fields, *, collapsible=True, collapsed=False)`
  in `Model.Meta.layout` is a section people can open and close; `collapsed` is `False` (open),
  `True` (closed) or `'empty'` (closed when every field is empty on an existing record). Plain
  `('Title', fields)` groups still work and always show. Each UI schema layout entry gains
  `collapsible` and `collapsed` (additive). The Controls form layout gets Can collapse and Starts
  per section, stored as optional `collapsible` and `collapsed`; older saved layouts load as before.
  Record pages remember each person's choice per model, with Reset layout in the More menu; on phones
  sections after the first start closed. Migration checksums do not change. The Playground's Project
  shows all three starting states, so its Planning group is split into Planning and Schedule
  ([docs/business-capabilities.md](docs/business-capabilities.md#collapsible-sections)).
- **Created and last modified on every record.** Every model table gains four framework columns,
  `evlet_created_at`, `evlet_created_by`, `evlet_modified_at` and `evlet_modified_by` (UTC times and
  user ids), filled in the same statement as every write: the record routes, the document pipeline,
  actions, moves, effects, void, unpost, repost and revise, `business.context(user)` and raw sessions
  (as the engine, or `session.actor`). Record details carry them as `_audit`, outside the ETag, with
  who as `{"id", "name"}` (null for the engine and for portal roles). The record page's Record info
  shows "Created 2 hours ago · Aslam" and "Last modified", with the exact time on hover and a click to
  switch to exact times, remembered per person; the Activity tab's times are relative too. A new
  browser component, `RelativeTime`, shares one minute clock that pauses in a hidden tab
  ([docs/model-engine.md](docs/model-engine.md#record-times), [docs/ui-framework.md](docs/ui-framework.md#relative-times)).
  What to change: run `evlet migrate`; framework migration `0004_record_times` adds the columns and
  fills them from the Journal, or from the time in the record id. A model may not declare a field with
  one of these names. Fixture scripts that need stable output wrap generation in
  `evlet.audit.fixed_times()`, as they do with `sequential_ids()`.
- **Period locks.** `Meta.period = "posting_date"` (and optionally `Meta.period_scope`) dates a
  model; admins set lock dates in Controls, Periods, with an audit trail. Records dated on or before
  a lock refuse every create, change, delete, move, correction and effect row, from HTTP and trusted
  Python alike, with `{"posting_date": "This date is in a closed period (locked up to 31-03-2026)"}`.
  `Session.unlocked_periods(reason)` and `Context.unlocked_periods(reason)` open them for a migration
  or repair, logged and audited. Mirror rows of a void or unpost in a closed period move to the first
  open date ([docs/model-engine.md](docs/model-engine.md#closed-periods),
  [docs/effects.md](docs/effects.md#closed-periods)). The Playground's ledger entries
  (`LedgerEntry`, `api = "read"`, written by Post to ledger, totalled by the Ledger balances report) and invoices show both; migration
  `0009_ledger` adds the table.
- **`Meta.api` sets how much HTTP surface a model gets.** `"full"` is the default and changes
  nothing. `"read"` lets callers (HTTP, MCP, Eva, the connector) read the model but write it in no
  way, for any role and whatever Controls overrides say; only trusted Python (sessions, actions,
  moves, services, jobs) writes it, so a posting function can be the only way into a ledger.
  `None` gives the model no surface: its routes answer 404 and it is left out of the schema,
  Discovery, Controls and the tool lists. A Link from a model callers reach to an `api = None`
  model must be `hidden=True` ([docs/model-engine.md](docs/model-engine.md#how-much-of-a-model-callers-reach)).
  A Python report may read an `api = None` model; its own `roles` decide who sees what it returns
  ([docs/reports.md](docs/reports.md#models-callers-cannot-reach)).
  Nothing to change for existing models. The schema adds `"api": "read"` on read-only models.
- **`evlet dev` creates the first admin.** On an instance with no accounts, `evlet dev` creates an
  active admin (`admin`, or `--user NAME`) with a random password that is never shown and prints a
  one-time sign-in link, so you type no password or two-step code in development. `--no-link` skips
  both. `evlet serve`, tenants and deployments never create accounts. New
  `evlet users set-password <instance> <username>` sets a real password from the server shell. It
  asks twice, enforces `security.password_min`, ends the user's sessions and is audited as `cli`
  ([docs/cli.md](docs/cli.md#development-sign-in-links)).
- **Report view.** Reports show in the browser at `/reports/<name>`, listed in the sidebar and
  at `/reports`: the filter bar, typed rows, totals, an optional chart, Export (CSV) and Print.
  New, all optional: `Report.group`, `model`, `sortable`, `chart` (`evlet.Chart`, a new root
  export) and `export`; `Column(source=...)` hides a column from people who cannot read that field;
  `run()` may return `totals` for every row and reads the sort as `context.sort`. New route
  `POST /api/_reports/<name>/export.csv`. The evaluate route adds `rules`. Existing reports need no
  change; a report with a filter field named `group`, `model`, `sortable`, `chart` or `export` must
  rename it ([docs/reports.md](docs/reports.md)).
- **Report groups and saved views.** `Report.group_by` keeps each group's rows together with a
  heading and a subtotal; `run()` may return `groups` with each group's count and totals for every
  row, and `context.sort` starts with the group column. People can save a report's filters and sort
  as named views. `DataTable` gains `rowClass`. A filter field named `group_by` must be renamed.
- **Print designs.** `PrintDesign` (a new root export) prints a record or a report to PDF through
  WeasyPrint: blocks in a header, main, footnote, footer and watermark region, Jinja templates in a
  sandbox with every value escaped, HTML for any single block, page number formats, and a footnote
  snapped to the bottom of the last page. `Application(prints=[...])` registers designs. Printing is
  the new `print` operation (admin by default; other roles when their Rule names it). It needs the
  optional extra `evlet[print]`; without it the print routes answer 503
  ([docs/printing.md](docs/printing.md)). Record views get a Print button with a PDF preview, and
  Controls gets a block designer for stored designs. These are additions; existing apps need no change.
- **API stability for apps.** [docs/api-stability.md](docs/api-stability.md) names the
  surface apps can rely on (the root exports, field keywords, lifecycle decorators and the
  `evlet.ui` names in the app docs), the deprecation rule and what stays internal.
  `tests/test_public_api.py` pins those names and signatures. A change needs a regenerated
  snapshot and an entry here.
- **Tool Engine and Eva exports.** The root exports gain `AI` (Eva's model settings),
  `Application(ai=...)` turns Eva on for an application, and `@api(..., tool=True)` offers an
  app service as a tool to Eva and MCP clients ([docs/eva.md](docs/eva.md),
  [docs/tools.md](docs/tools.md)). These are additions; existing apps need no change.
- **`DevSignIn` export.** The root exports gain `DevSignIn`, which serves the one-time
  development sign-in links of `evlet dev` and `evlet playground` ([docs/cli.md](docs/cli.md#development-sign-in-links)).
  Never wrap an application you serve with it.
- **Evlet upgrades go through `evlet migrate`.** `apps.lock` and the installed manifest
  now record the Evlet version and its last framework migration. An instance migrated
  with another Evlet version refuses to serve and asks for `evlet migrate`, which backs
  up the database and runs ordered, checksummed framework migrations
  (`evlet_framework_migrations`) on the staged copy. Existing instances need one
  `evlet migrate` after upgrading; it adopts today's startup tables with no other change
  ([docs/app-upgrades.md](docs/app-upgrades.md#upgrade-evlet)).
- **Field retirement, safe type changes and data steps.** `RetireField` removes a field from the
  models and API while keeping its column and values; `DropField` deletes a retired field's column
  only with `allow_destructive=True` (`evlet migrate --allow-destructive`, which keeps a backup).
  `AlterField` converts Integer to Decimal, widens a decimal scale, lengthens text and adds Select
  choices, validating every stored row in the migration transaction. `RunData(function)` runs a
  trusted data step right after its own migration; `RunData(..., maintenance=True)` may write
  confirmed and posted records only with `--allow-maintenance`, and each such step is audited. A
  retired Link drops its foreign key. `--plan` suggests RetireField and AlterField
  ([docs/model-engine.md](docs/model-engine.md#retiring-and-dropping-fields)).
  `RetireField`, `DropField`, `AlterField` and `RunData` are new root exports.
- **Migration plans.** `evlet migrate <instance> --plan` and `database.plan(migrations)` show the
  pending migrations and each model, field and property that differs from the models, with a
  suggested migration; nothing is written. `evlet makemigration <app> --instance <instance>`
  writes that suggestion into a flat app. A mismatch error now lists the same differences.
  `AddTable(parent, name, ChildClass)` adds a child table without hand-written owner and position
  fields, and `CreateModel` no longer compares the Python class name ([docs/model-engine.md](docs/model-engine.md#planning-a-migration)).
  `AddTable` and `MigrationPlan` (what `database.plan()` returns) are new root exports.
- **Team and hierarchy row scopes.** New root exports `Hierarchy` and `any_of`. `Hierarchy(model, parent=...)` declares a reporting tree, and
  `Hierarchy.team(field)` is a row scope over the user's linked record and everyone below it at any
  depth, resolved in SQL with a recursive query on SQLite and PostgreSQL. `any_of(...)` combines
  row scopes and filter objects with OR. They apply on every surface a row scope covers
  ([docs/business-capabilities.md](docs/business-capabilities.md#team-and-hierarchy-scopes)). List pages
  now check Link values for the whole page at once instead of per row.
- **Light and dark themes.** Every screen of the browser UI comes in light and dark, or
  follows the device (`theme` in Settings, Appearance: `"light"`, `"dark"` or `"system"`, now
  the default). The server writes the theme on `<html>` before the page paints, so nothing
  flashes, and the sign-in page keeps the browser's last theme. The sidebar, account menu,
  palette and Docs switch it. The config block gains `"theme"`. Components use colour tokens
  only, and a test checks WCAG AA contrast of the token pairs in both themes
  ([docs/ui-framework.md](docs/ui-framework.md#themes)).
- **List filters like Frappe's.** The Filter button opens rows of field, condition and value,
  any number of them, combined with AND. The field picker searches hundreds of fields and each
  child table's fields; conditions follow the field type (like, in, between, timespan and more);
  values use the form's own pickers; active filters show as chips and stay in the address
  ([docs/ui.md](docs/ui.md#filters)). Links in the old `?f={"field": value}` form still open.
- **Filter lists and child table filters.** `filters` also takes a list of `[field, operator, value]`
  conditions and child groups `{"child": "prices", "match": "any" | "none", "where": [...]}`,
  compiled to `EXISTS` / `NOT EXISTS`, in Python queries and the list, aggregate and measures
  routes ([docs/model-engine.md](docs/model-engine.md#filter-lists-and-child-tables)). New
  operators: `between`, `nin`, `nicontains`. Each field kind now takes only its own operators
  (a `gt` on a Boolean or `icontains` on a number is a 422), and the schema sends them as each
  field's `operators`; child tables in the schema say whether they are `filterable`. A query
  takes up to 50 conditions (was 20 filters). Filtering on a field or child table the role
  cannot read is a 403, the same as an unknown one.
- **Pickers.** Select fields, and every select on the Settings and Controls pages, open Evlet's own list (current choice ticked, search for long
  lists, Clear for optional fields) instead of the browser's menu. Date, date-time, time and
  duration fields keep their typed input and add a calendar or steppers. List pages filter
  date fields by range, with presets.
- **Typed dates and times** are read the forgiving way: `120627`, `1/6/27`, `1206`, `today`,
  `+3`, `935`, `12.35pm`, `9am`, `120627 1235`, `90m`, in the day, month and year order of the
  person's date format. Impossible dates are refused, never guessed ([docs/ui-framework.md](docs/ui-framework.md#typing-dates-and-times)).
- **Durations** also take `1.30` and `1:30:20` (hours, minutes, seconds), decimal units such as
  `1.5h` or `1.5m`, and words such as `1 hr 30 mins`. A dot without a unit needs two minute digits,
  so `1.5` is refused.
- **Playground items.** A new Item model has about 200 fields and two child tables (prices,
  barcodes), to try filters at scale. Run `.venv/bin/python -m examples.playground.migrate` (migration `0005_items`) on an
  existing Playground database.
- **Playground invoice numbers.** New Playground invoices are named by a naming series,
  `INV-{YYYY}-{#####}` dated by the posting date (`INV-2026-00001`), in a new `number` field
  (migration `0006_invoice_number`; run `python -m examples.playground.migrate`). `reference` is now
  labelled Customer reference. `/api/schema` marks a naming series field with `naming: {"field": ...}`
  and the placeholder `Assigned on save`, and the New screen shows that field read only
  ([docs/sequences.md](docs/sequences.md#naming-series)).

### Changed


- **Changed default: a State shows as a header badge, not a stage bar.** `State(..., show=...)`
  takes `"badge"` (the new default: a coloured status badge in the record header, with the moves
  as header buttons), `"bar"` (the stage bar, as before, for pipelines such as draft, submitted,
  approved) or `"none"`. What to change: add `show="bar"` to a State that is a real pipeline
  to keep the bar. The schema's `state` entry adds `show`. The badge and the bar now offer only
  the moves this person may take now; states only the system reaches, and moves for other roles or
  whose condition is not met, are plain text. `_moves` now also blocks every move on a
  `Meta.api = "read"` model (`You cannot change this record`) and on a record in a closed period
  (`This record is in a closed period`). The testCRM deal and the amendment quote declare
  `show="bar"` ([docs/states-actions.md](docs/states-actions.md#how-a-state-and-a-status-show)).
- **Breaking: record ids are text.** New records get a 63-bit, time-ordered id (milliseconds since
  01-01-2026 and 21 random bits), stored as before in the `INTEGER` primary key. Outside the
  database an id is always 13 lowercase Crockford base32 characters, such as `01hc1sd9vc1ye`: JSON
  bodies, Link values, `{"model", "id"}` References, row ids, the journal, events, Slips' `source`,
  Eva and MCP tool arguments, and URLs (`/api/invoice/01hc1sd9vc1ye`, `/app/invoice/01hc1sd9vc1ye`).
  Ids are never JSON numbers, because a browser cannot hold 63 bits in a number. Existing rows keep
  their ids (42 shows as `000000000001a`); nothing is rewritten.
  What to change: clients read ids as strings and build URLs from them. A decimal id in an API path
  is now a 404 (the browser turns an old `/app/invoice/42` bookmark into the text id). Writes still
  take a JSON integer for a Link or a row id. In Python, `record.id` and Link values are
  `RecordId` (a new root export), an `int` whose `str()` is the text id; `Database.get` takes either
  form, and services and actions may take `RecordId` arguments
  ([docs/model-engine.md](docs/model-engine.md#record-ids)).
- **Breaking: every record has a standard name and title.** Record models (not Table rows) gain
  two framework columns next to `id`. `name` is the human identifier, unique per model, filled by
  `Meta.naming`: a naming series (which now fills `name` by default), the model's
  `autoname(self, context)` method (`Naming()`), or people typing it (`Naming(manual=True)`), on
  create, on confirm (`assign_on="confirm"`, gap-free numbers for confirmable documents) or on
  entering a state. Once assigned, people who may write the field can change it with a normal
  edit; Links point to the id, so nothing else changes. `title` is the display text Link pickers,
  lists, search and Eva show: a declared `title` field, or kept in sync from `Meta.title_field` or a
  `Meta.title = "{first_name} {last_name}"` template, falling back to the name, then the text id.
  Record pages show the title as the heading and the name in mono next to it
  ([docs/model-engine.md](docs/model-engine.md#name-and-title)).
  What to change: an `Application` refuses a model that declares its own `name` field; rename it in
  an app migration, such as `RenameField('customer', 'name', 'customer_name')`, and point
  `Meta.title_field` at it (or rename it to `title`). Then run `evlet migrate`: framework migration
  `0003_record_names` adds the columns to existing tables and fills every title. API responses and
  the schema now include `name` and `title`; the schema's `title_field` is `title`.
  The Playground's `Customer.name` is now `customer_name`, `Employee.name` is `full_name`, `Project.name` is `title`, and invoice
  numbers moved from `number` into `name` (migration `0008_record_names`).
- **Link filters that read another field follow it in the form.** For a Link such as
  `branch = Link(Branch, filters={"company": Ref("company")})`, the picker now lists only records
  that fit the form's current, unsaved values (before, it ignored `filters`), and asks for an empty
  referenced field first. Changing a field checks every Link whose filters read it, on the same row
  and on Table rows through `Ref("parent.<field>")`: a value that still fits stays, one that no
  longer fits is cleared with a note such as "Cleared because Company changed", and chains clear
  together. The field error is plainer: "Port office is not in Summit Services." with one filter
  and readable titles, "Choose Company first." for an empty referenced field, and otherwise "This
  Branch doesn't fit the other values on this form." It never shows a title the role cannot read.
  What to change: code or tests that match the old text "The selected record does not match the
  current filters" match the new messages; the error stays a field error with status 422. The
  schema sends `behavior.<field>.filters` for models with no other rules too. The Playground's
  Invoice has a Project limited to the customer's projects (migration `0010_invoice_project`)
  ([docs/business-capabilities.md](docs/business-capabilities.md#link-filters), [docs/ui.md](docs/ui.md#link-filters)).
- **Columns menu with drag and a pinned range.** Child tables and reports share one Columns menu:
  drag a grip (mouse, touch, or Space and the arrow keys) to reorder, and drag columns above a
  divider, or use the pin button, to pin any number of them on the left. `TablePrefs` gains an
  optional `pinned` count; prefs saved without it keep the first column pinned. The up and down
  arrow buttons are gone. Its dragging uses the shared `dragList`, as the form layout editor does. A child table's Focus view keeps a compact record header (mark, type,
  title, number, Back to form) above the table and the save bar below it (the `heading` option of `ChildTable`).
- **Where a Currency field's code comes from.** `Currency(currency=...)` also takes
  `"link.field"` (a field of a linked record) and `"settings.app.key"` (an app setting). In
  `/api/schema`, `currency` now only ever holds a code; a field on the record (`"code_field"`)
  is sent as `currency_source: {"field": "code_field"}` instead of `currency: "code_field"`.
  A client that read the field name from `currency` should read `currency_source`.
- **Account links are audited.** `LocalAuth.link_user` and `unlink_user` take an `actor` and write
  each change to the Controls audit log (area Users); `evlet link-user` records `cli`.
- **Form fields** are 40 px tall with a soft focus ring, a 14 px label and a blue required
  mark. `help_text` now shows under the input (an error takes its place). The new
  `tooltip=` field option puts its text behind a ? icon at the end of the input; Controls can
  change it and exports it.
- Integer and Decimal fields show their `suffix` after the value at rest too (`5.00%`).
- **Compact Related block on the record page.** The side panel lists each related model as a
  36px row: icon, name, count and chevron, as real links. Relations with records come first, most
  records first. Those with none fold into one line, "No records: ...", and each name still opens
  the Related tab. Five rows show at first and "Show all" expands the rest; the choice is kept per
  user and model. The count no longer runs into the name.
- **Offline docs show user guides only.** Handoff plans, the model engine ledger,
  milestone notes, the specification and the testCRM notes moved to `docs/internal/`.
  They are no longer in the wheel or shown by `evlet docs`. The contributor guide moved
  to `CONTRIBUTING.md`.
- **Smaller source distribution.** The sdist no longer carries tests, scripts, lock
  files, internal docs or the browser test fixtures. It keeps what building and
  installing Evlet needs, including the browser interface source.
- **Version 0.3.0.** Generated apps and the examples require `evlet>=0.3.0,<0.4`. Apps that
  declare `evlet>=0.2.0,<0.3` must widen the range to install with 0.3.0.

### Fixed

- **Focus in the editable child table.** Clicking a cell focuses it while another cell is
  being edited, with one active cell at a time (a cell that can't be committed keeps the focus,
  and an unchanged cell never blocks). Tab and Shift Tab reach every editable cell from a cell in
  navigation mode too, then go on to Add row. A choice from a dropdown or Link search returns the
  focus to its cell, so Tab and Enter continue as the footer says.
- **Letter avatars and record tiles are centred again.** A record page's meta row styled
  every nested `span`, which turned the avatar and the rounded-square tile there into flex boxes
  with the letter off-centre. The rule now reaches only the row's own items, and `.avatar` and
  `.org-mark` set `line-height:1`.
- The Link field's record mark no longer stretches across the input.
- **Unreadable dates, times and durations keep the field's value** on blur or Enter and say what
  was typed ("Couldn't read 1.62 as a duration"). The API keeps accepting only the stored forms.
- The record state bar no longer stacks vertically: the time picker's spinner had reused its `.stepper` class and now uses `.time-stepper`.

## 0.2.0 (02-11-2026)

This release replaces the browser interface with Evlet's own UI framework, adds the
model engine, and adds Controls and Settings for administrators. It covers #30 to #63.

### Upgrading from 0.1.2

- **Apps must allow Evlet 0.2.** Apps declaring `evlet>=0.1.2,<0.2` refuse to load on
  0.2.0. Change the dependency to `evlet>=0.2.0,<0.3`. New apps get this by default.
- **SolidJS is gone.** The browser interface is Evlet's own UI framework
  (`frontend/src/evlet/`). The Vue harness, the Projects demo and the Python page renderer
  were removed from it. Python pages, native panels, report screens and form dialogs keep
  their Python APIs, routes and tests but are not shown in the browser
  ([docs/ui.md](docs/ui.md#not-rendered-by-this-interface)).
- **Security defaults changed.** Sessions last 12 hours (was 8) and sign-in locks after 5
  failures (was 10). Set `security = Security(session_hours=..., lockout=...)` in an
  instance's `config.py` to choose other starting values. Saved settings looser than the
  new values are clamped.
- **Two-step sign-in is enforced.** People in a role that requires two-step set it up at
  their next sign-in and can't use the app until they do. Admins should sign in right
  after upgrading. `evlet users reset-two-step` resets it from the shell.
- **New core dependency: `cryptography`.** It encrypts connector secrets, mailbox
  sign-ins and two-step secrets (AES-GCM). It installs automatically with Evlet.
- **Proxies must be listed.** `X-Forwarded-For` is only trusted from addresses in
  `Instance(trusted_proxies=...)`. Behind a reverse proxy, add it, or every visitor looks
  like the proxy. Controls warns when it sees forwarded headers from an unlisted proxy.
- **Run `evlet migrate`** for each instance after upgrading. Migrations never run from
  the browser.

### Model engine

- Expressions: automatic rounding to the target field, dates (`today()`, `days()`, date
  arithmetic), Link hops, `when(cond, a, b)`, with a TypeScript mirror in the browser.
- Rules: `initial`, `applies_when`, `required_when`, `locked_when`, `Recompute`
  migrations, and new field types (Phone, Percent, Time, Duration, Tags, Ref).
- Child tables (`Table`) with row limits, paging and batched loading, tested at 1,000 rows.
- States and moves: `State`, `Move`, locked and final states, asked values, `available_when`.
- Corrections: `void`, `revise`, and admin-only `unpost` and `repost` of posted documents.
  Unpost keeps the document number and cancels what was posted with reversing entries. A
  record that has ever posted can't be deleted.
- Effects: posting rows with `Effect`, `Swap` and `Negate`, balance checks and
  `evlet effects check`.
- Sequences: gapless numbering with period resets and scopes.
- Journal: an append-only record history with a hash chain and `journal verify`.
- Measures: totals and counts declared on models, served by one aggregate API.
- Meaning: `key_fields`, `relations`, `Meta.attachments` and `view_for`.
- Posted records are never recalculated by `Recompute` unless an admin runs
  `evlet migrate <instance> --recompute-posted`. Void records are always skipped.

### Browser interface

- Evlet's own UI framework: fine-grained reactivity, no virtual DOM, no runtime
  dependencies.
- A component library, and generated screens for every model: lists, boards, record
  pages, child tables, a command palette, actions and the journal.
- Login, Docs, the account menu and personal settings.
- Fonts are bundled under the OFL. The dark theme is hidden for now.
- testCRM (`examples/testcrm`), a CRM written as `models.py` only, used as the working
  example and end-to-end test.

### Routing and hosts

- A route table with reserved paths, `routes.py` and `evlet routes`.
- Sub-hosts, sign-in realms (desk, portal, public) and their cookies, API scopes, static
  and server frontends, and a customer portal through `evlet_user_links`.
- `evlet proxy` generates reverse-proxy configuration.

### Controls (administrators)

- Users, roles, permission overrides, an append-only audit log and a config version.
- Fields added from Controls (`x_` fields) are visible to every staff role by default.
  Portal roles see them only when an admin grants them.
- Naming series over the gapless sequence counters.
- Apps, routing, jobs and audit pages, and export of overrides as code.
- Opening Controls can be given to a role without making it admin. Only admins grant admin.
- An override that no longer validates denies access and is listed under Needs attention.
- Any change in Controls asks for a two-step code, valid for 10 minutes on that session.

### Settings and security

- Two-step sign-in (TOTP) with backup codes, checked per session.
- Sensitive personal settings ask for a two-step code, or the password when two-step is off.
- Ending access goes through one function. A password change and "Sign out everywhere"
  ask whether to also revoke API keys. A two-step reset, deactivation and break-glass
  always revoke them.

### Connectors and integrations

- Connectors with encrypted secrets, email accounts and personal API keys.
- Remote MCP for AI assistants, with OAuth 2.1 and PKCE.
- Personal mailboxes that link messages to records for models that opt in with
  `Meta.mail`.

### Fixes

- A dropped IMAP connection during sign-in no longer deletes the saved mailbox sign-in.
- The PostgreSQL CI job runs every test file that has PostgreSQL variants.
- Deleting a parent with more than 100 legacy child rows no longer fails.

## 0.1.2

Merged UI and CLI release. See the `archive/release-pypi-0.1.2` tag.
