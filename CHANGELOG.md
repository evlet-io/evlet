# Changelog

Evlet is pre-1.0 and not production ready. From 0.3, a public name or argument that is
removed or changed keeps working with a `DeprecationWarning` for at least one minor release
first ([docs/api-stability.md](docs/api-stability.md)). The notes below say what to change.

## 0.4.1 (10-11-2026)

### Fixed

- **Remote MCP works behind the generated proxy.** The Caddy and nginx files from
  `evlet proxy` and `evlet setup --host` refused every `/_/` path, so `/_/mcp`, the OAuth
  sign-in at `/_/oauth/*` and the mailbox sign-in return at `/_/mail/*` got 404, and a
  Claude custom connector could not connect. They now pass exactly those routes
  (`evlet.app.PUBLIC_PATHS`) and still refuse the rest, such as `/_/tls-allowed`. A test
  fails if a new `/_/` route is not in either group.
- **The on-demand TLS check answers on instances with accounts.** `/_/tls-allowed` fell into
  the MCP routes and answered 404 for every host.
- **`evlet setup --host --apply` replaces outdated installed files.** It says when the copy in
  `/etc/caddy/` or `/etc/systemd/system/` is out of date, installs the new one, reloads Caddy,
  and restarts the service when its unit changed.

### Added

- `evlet serve` logs one warning line at start-up when the installed
  `/etc/caddy/evlet-<instance>.caddy` blocks a public route, with the command to fix it.
- `evlet setup --host` never needs `--ip`. Behind NAT, a host that points at a public address
  the server cannot see as its own gets one warning line and setup goes on. It stops only for a
  host with no record, or with only private records while the server has a public address.
  `evlet proxy --check` and Controls, Routing follow the same rule (`may point here`).
- A `--ip` given to `evlet setup --host` is kept in `deploy/server.json` after a passing check and
  used by later runs. A new `--ip` replaces it.
- After `--apply` reloads Caddy, `evlet setup --host` checks for about half a minute whether
  `https://<host>` answers and prints the result. It never fails the command.
- Controls, AI, MCP: a **Check** button shows whether the public address reaches `/_/mcp`,
  with a short reason when it does not. Admins only, on demand.

### Upgrading

A site set up with `evlet setup --host` on 0.4.0 needs its Caddy file updated. As the service
user: stop the service, upgrade Evlet in the project's `.venv`, run `evlet migrate <instance>`,
then run the same `evlet setup ... --host ... --apply` command again
([docs/cli.md](docs/cli.md#serve-a-site-on-a-domain-in-one-go)). With nginx, write the file again
with `evlet proxy <instance> --nginx` and reload nginx.

## 0.4.0 (10-11-2026)

This release adds decimal places per currency with money stored at 6 places, the compact record page
without tabs, child table tools and section rows, image fields, derived values, ledger models,
workspaces, list tabs and `evlet setup` in one go. It removes the Python page API and native panels,
which the browser never used. It covers #120 to #159. Evlet is still not production ready.

### Upgrading

- **Run `evlet migrate` for each instance before you serve it.** An instance migrated with 0.3.0
  refuses to serve until you do. Stop the server first. It backs up the database, then runs the new
  framework steps `0006_fixed_ids` (a trigger on every model table that refuses an id change) and
  `0007_money_scale` (every stored Currency amount moves to 6 places, exactly), then the app
  migrations ([docs/app-upgrades.md](docs/app-upgrades.md#upgrade-evlet)).
- **Apps must allow Evlet 0.4.** Change an app's dependency from `evlet>=0.3.0,<0.4` to
  `evlet>=0.4.0,<0.5`. New apps get this by default.
- Then work through the Breaking list below. [docs/app-upgrades.md](docs/app-upgrades.md#from-03-to-04)
  has the same steps as a checklist.

### Breaking

- **Record pages have no tabs.** `Tab`, `Meta.tabs` and `Meta.default_tab` are removed. They are
  removed without the usual deprecation period, by decision of the project owner, because no apps
  built on Evlet have been released yet. Using them fails when the model is defined, with a message
  that points to `Main` and `Section`. Lay a record out with `Main(fields, *tables)` first in
  `Meta.layout` and `Section(...)` and the other blocks after it. A model without `Meta.layout` puts
  every field in the primary area, in declaration order, followed by its Tables; a layout of
  sections only uses its first section as the primary area and the others as "more information".
  Activity moves to the side column (a drawer in a narrow window); Revisions, Related and Postings
  are rows of "more information". The record address no longer takes `?tab=`, and a list's side
  panel no longer writes `?rtab=`. `record_layout` in `/api/schema` is `{"blocks": [...]}` only, with
  no `compact` key. Controls layouts no longer take a record tab order; one saved before keeps its
  sections and panel. The side panel's narrow child rows (`ChildRows`), the Key fields and Related
  side blocks, sticky record tabs and `keepTabPlace` are gone.
- **Currency amounts are stored at 6 places.** After `evlet migrate`, values still read and travel
  as before (`"12.50"`), applied migration checksums and saved calculation versions do not change,
  and the largest Currency amount is 9,223,372,036,854.775807. A `Currency` field's schema `scale` is
  now 6, the storage scale: format from `currency_decimals` instead. Apps started outside an instance
  move on startup or in `database.migrate()`. Code that rounded money with `quantize(Decimal("0.01"))`
  or `round(x, 2)` should use `round_money(value, code)` so KWD and JPY amounts are right.
- **Writes refuse amounts with more places than their currency.** For example "Use at most 3 decimal
  places for KWD". Nothing is rounded silently (see Decimal places per currency below).
- **The list board is opt-in.** A list offers the Board view only when `Meta.board` names its State
  field, such as `board = "stage"`. Apps that relied on the automatic board for every model with a
  State must set it. An old `?view=board` link opens the table. `Meta.board` on any other field fails
  registration, naming the model.
- **Python pages are gone.** The Python page API in `evlet.ui` (`PageView`, `ListView`, `FormView`,
  `Column`, `ViewRegistry`, the compiler, component contracts and page expressions) and the
  `GET /api/ui` and `GET /api/ui/resolve` routes are removed. Delete those definitions and shape the
  generated screens with `Model.Meta` (`layout`, `list_fields`, `open_in`, `panel`). An
  `AppDefinition` may still pass `views=()`; a non-empty `views` or a `ui` registry is refused at
  start-up. `Application` and `App` no longer take `views` or `ui`. Clients that called `/api/ui`
  read `/api/schema` instead. The Playground's Projects demo and Layouts pages go with it.
- **Native panels are gone.** Remove `.ts` and `.js` modules from an app's `views/` folder and the
  matching `package-data` line from its `pyproject.toml`. `native_panels` in `/api/schema` and
  `/assets/apps/...` are removed, and a build no longer runs Node for them.
- **`evlet new` is gone.** Use `evlet setup myproject --app notes --instance acme --dev`. The nested
  app layout and `evlet app <name> --legacy` stay.
- **`evlet.metadata` is gone.** `field_schema`, `label_for`, `ui_schema` and `view_for` live in
  `evlet.ui.metadata`, which is internal ([docs/api-stability.md](docs/api-stability.md#internal)).
- **`evlet.calc.Document` loses `observe`, `observe_table`, `observe_rules`, `subscriptions`,
  `explain` and `dispose`.** The server settles a document once and reads its snapshot; the browser
  engine keeps all of them.
- **A caller-supplied record `id` is refused** on every create and update path with
  `ValidationError({'id': ...})`, and HTTP POST and PATCH answer 422 on `id` instead of 403 (see
  Fixed ids below).

### Added

- **Decimal places per currency.** Each currency code has 0 to 6 places: ISO 4217 by default (AED 2,
  KWD 3, JPY 0), changed or added by an admin in Controls, Currencies (for example a client's own
  unit with 5). Finite and legacy formulas, table sums and totals round to the currency each row
  resolves through its currency source, and print, CSV and every screen show exactly those places.
  Trusted code uses `currency_decimals(code)`, `round_money(value, code)` (half up) and
  `currency_unit(code)` instead of hard-coding 2. An app with its own currency list writes it to the
  framework with `set_currency_decimals(app, {...})`. Money kept in `Decimal(scale=4)` moves to
  `Currency` with `AlterField`, exactly. Lowering a code's places is refused (409) while stored
  amounts would lose digits; `round_stored_money(app, code, places, actor=...)` is the explicit step
  that rounds them. `/api/schema` sends `currency_decimals`. When a saved record was calculated with
  older formulas, the browser asks for a recalculation instead of saying someone else changed it.
  The Playground adds a KWD customer and a 5-place unit
  ([docs/model-engine.md](docs/model-engine.md#decimal-places-per-currency)).
- **Currency display per site.** Controls, Currencies sets how each code is shown: a text symbol
  (`$`, `€`, `د.إ`) or a small PNG or WebP image (at most 32 KB and 128 x 128 pixels, SVG refused),
  before or after the amount, with or without a space. It applies to forms, lists, boards, totals,
  measures, reports and print designs. Only admins change it, and every change is audited.
  `/api/schema` sends `currency_display`, and trusted code can use `set_currency_display(app, {...})`.
  Report CSV names a currency column's code in its header, `Total (AED)`
  ([docs/controls.md](docs/controls.md#currencies)).
- **The compact record page.** `Main(fields, *tables)` as the first block of `Meta.layout` is the
  primary area, with no heading; every block after it is "more information", shown as rows with a
  one-line summary while closed. `Meta.cards` names up to five derived values (fields with
  `compute=`, `fetch=` or `query=`, and per-record measures) drawn as number cards above the record.
  `Section(..., description=...)` adds a line under a section's title. The side column holds
  Attachments, Activity and Record info. All of it is checked when the registry is built, and
  `/api/schema` sends `cards` (cut per role) and `description`
  ([docs/ui.md](docs/ui.md#compact-record-page)).
- **Record layout blocks.** Besides `Main` and `Section`, `Meta.layout` takes `Table` field names,
  `Related(model, link=None, filters=..., fields=..., limit=..., order=..., label=...)` lists of
  related records, `Html(...)` text with `{field}` values, and `Chart('stat' | 'bar' | 'line', ...)`
  blocks over a related model's declared measures. Filters use the list syntax, and `Ref('field')`
  compares with a field of the record. Related rows and charts come from the list and aggregate
  routes, so row scopes and field permissions apply. Html keeps to the rich text allowlist and
  inserts record values as text only. Controls, Export as code writes the full `Meta.layout`
  ([docs/ui.md](docs/ui.md#record-layouts)).
- **Child table tools, row editor and section rows.** A record's table has Focus and Columns buttons.
  A row's Edit opens the row editor in focus view: the rows as a list beside one form for the row,
  with previous and next, Delete line, Insert below and Done. `Table(..., sections=True)` lets a
  Table hold section rows, headings such as "Goods" that group the rows below them, each with its own
  sums. Iterating `self.lines` still yields only real rows, so existing app code is unchanged;
  `self.lines.sections()` gives `(label, rows)` pairs. The existing routes create, rename, move and
  delete section rows with the same checks as rows. They are stored in `evlet_sections`, so turning
  them on needs no migration. Print, the Journal and revisions include them
  ([docs/model-engine.md](docs/model-engine.md#section-rows)).
- **Image fields: `Image()` and `Meta.image`.** A field holds an image, such as an item photo, as a
  private attachment of the record: PNG, JPEG, GIF or WebP checked by their bytes (SVG refused), up to
  Controls `files.image_max_kb`. A person who cannot read the field cannot load the image.
  `Meta.image` names the record's avatar, shown left of the title. Add one with
  `AddField(model, name, Image())` ([docs/model-engine.md](docs/model-engine.md#image-fields)).
- **Derived values: `derive()` and `Meta.derives`.** A document builds stored values that formulas
  cannot express, such as rows built from a linked record, or a field on each row of a Table. It runs
  on every write and preview, before calculations. The fields and Tables it sets are refused to
  callers (`DERIVED_FIELD_WRITE`, 422) and marked `read_only` in `/api/schema`. `validate()` stays for
  checks only ([docs/model-engine.md](docs/model-engine.md#derived-values)).
- **Closing checks: `before_close(self, to)`.** One read-only hook that runs before a move into a
  final state, a void, a cancel and a delete, on every surface, and refuses with a `ValidationError`
  before anything is written
  ([docs/states-actions.md](docs/states-actions.md#before-a-record-closes)).
- **Cancelled states: `State(..., cancelled=...)`.** Names the final states that mean a record was
  called off. On a voidable State, `"void"` counts without being listed. Lists and boards strike
  cancelled records through, and the record shows when, who and why. Locked or final states show a
  lock, and the starting state a Draft pill
  ([docs/states-actions.md](docs/states-actions.md#cancelled-states)).
- **`self.context.session` in run methods and actions.** A Move's `run` method and an `@action`
  handler can write other rows, such as ledger rows, in their own transaction, with `tx.lock`,
  `tx.insert_many`, `tx.reserve_ids` and `tx.aggregate`. Everything commits or rolls back together.
  It is open only while the handler runs; every other context refuses it with `AccessError` (403)
  ([docs/states-actions.md](docs/states-actions.md#the-transaction-session-in-run-methods-and-actions)).
- **Ledger models.** `Meta.ledger = True` stores a model lean: a database-assigned integer id and the
  declared columns only, with no public surface. Options are `append_only`, `journal`, `timestamps`,
  `indexes` and `period`. An append-only table refuses `UPDATE` and `DELETE` with `ConflictError` on
  SQLite and PostgreSQL. `tx.insert_many(model, rows)` inserts rows with one prepared statement, and
  `tx.reserve_ids(model, count)` reserves ids so rows of one batch can link to each other. New
  migration `SetAppendOnly`, and `evlet ledger list --instance NAME`
  ([docs/ledger.md](docs/ledger.md)).
- **Fixed ids.** Only Evlet makes record ids, and ids never change. Every model table gets a database
  trigger that refuses an id change (framework step `0006_fixed_ids`). `evlet ids check INSTANCE`
  reports rows with an empty, zero or negative id and tables without the trigger; it never rewrites
  ids ([docs/model-engine.md](docs/model-engine.md#only-evlet-makes-ids)).
- **Composite and unique indexes on every model.** `Meta.indexes`, such as
  `[(("station", "on_date", "kind"), {"unique": True}), ("on_date",)]`. A write that breaks a unique
  entry raises `ValidationError` (HTTP 422). `AddIndex(table, ("a", "b"), unique=True)` adds one
  ([docs/model-engine.md](docs/model-engine.md#composite-and-unique-indexes)).
- **Transaction locks.** `tx.lock(*keys, timeout=None)` holds named locks on business keys until the
  transaction ends. A wait past `timeout` raises the new `LockTimeout`.
- **`tx.aggregate(...)`, paged aggregates and long `in` lists.** The transaction session runs the same
  aggregates as `database.aggregate` and sees its own writes. Both take `limit` and `after` to walk
  any number of groups a page at a time. Trusted session queries take `in` and `nin` lists of up to
  30,000 values. HTTP, `Context`, Eva, tools, MCP and the connector keep their limits
  ([docs/measures.md](docs/measures.md#paging-through-many-groups)).
- **Progress for long jobs.** A job handler reports `context.progress(done, total, message)`.
  `Task(..., resumable=True)` runs a long job in steps, each in its own transaction, and resumes from
  the last committed checkpoint. Controls, Jobs and `GET /api/_jobs/<id>` show progress, and
  `evlet jobs list --instance NAME` prints it
  ([docs/jobs-realtime.md](docs/jobs-realtime.md#progress-and-long-jobs)).
- **Workspaces.** `Workspace`, `NavGroup` and `Shortcut` declare named sidebars of models and
  shortcuts to a filtered list or board, a report, a slip or an address, in an app's `workspaces.py`
  or through `Application(workspaces=...)`. `/api/schema` has `workspaces`, with only the items each
  role can open. Admins, and roles with the new Controls access "Customize workspaces", can reorder,
  hide, rename and add items and workspaces per site; overrides never widen access.
  `GET /api/_settings/me` reports `customize_workspaces`
  ([docs/ui.md](docs/ui.md#workspaces), [docs/controls.md](docs/controls.md#workspaces)).
- **List tabs.** `Meta.list_tabs` names the State, Select or Link field whose values are the list's
  tabs, and `Meta.list_tab` the tab it opens on. `list_tabs = None` turns them off. Counts come from
  `GET /api/{model}/list_tabs`, under the list's filters and row scope. Controls sets both per
  instance ([docs/ui.md](docs/ui.md#list-tabs)).
- **`evlet setup` in one go.** `evlet setup myproject --app notes,crm --instance acme --dev` creates
  the project, apps and instance, installs the apps and starts `evlet dev`. `--get` fetches apps
  from Git, `--trust` answers the trust questions, and `--host erp.example.com` prepares a server
  install with a Caddy site and a systemd unit under `deploy/` (`--apply` runs the steps). Running the
  same command again skips finished steps ([docs/cli.md](docs/cli.md#setup-in-one-go)).
- **Smaller model options.** `Integer(grouping=False)` shows a number without thousands separators.
  `Naming(..., label="Project number")` labels the standard `name` field. `Meta.focus` (and `focus`
  on a `Slip`) names the field a new form starts in. `Meta.duplicate = False` hides Duplicate, and
  `copy=False` on a field or `Table` keeps it out of a duplicate. `Meta.summary = False` leaves out
  the summary strip, `Meta.side_blocks` keeps attachments (`files`) or the automatic Related row (`related`), and `Meta.panel_summary = False`
  leaves the cards out of a list's side panel. Measures take `trend=` and `color=`. Reports say
  `source_kind`. All are checked when the registry is built.
- **Choose what a list's side panel shows.** `panel=False` on a field, `Section` or `Table` keeps it
  on the full page only, and `Meta.panel` names the only parts the panel keeps, in order. Controls,
  Form layout sets the same per instance. It is display only
  ([docs/ui.md](docs/ui.md#what-a-side-panel-shows)).
- **In the browser:** saved filters on lists, removing a file from a record, More > Copy record ID,
  Change password from the account menu, a history button for recent records, a notification
  centre behind the bell, and a link from Controls back to the app.

### Changed

- **Controls, Export as code writes code that works.** It writes `Meta` options on the model's class
  (`layout`, `list_fields`, `open_in`, `list_tabs`, `list_tab`) instead of `FormView` and `ListView`
  code the browser ignored.
- **Sign-in lands on the first list, not Docs.** After sign-in, and at any address that matches no
  route, the app opens the start page saved in Settings, else the first list the role can open.
- **Aggregate filters on row-scoped Links.** `GET /api/{model}/aggregate` allows an `eq` or `in`
  filter on a Link to a row-scoped model when every id is a record the caller can read
  ([docs/measures.md](docs/measures.md#permissions)).
- **`Related` and `Chart` take more arguments.** Existing calls are unchanged.
- **The record page shows each fact once.** Key fields show in the header, and the summary strip
  holds only calculated values and measures the header does not show. Record info no longer shows
  the record id.
- **Related records load when shown, five at a time,** with a small pager and View all.
- **Lists run flush to the main area,** and the filter panel docks beside the table as a side
  panel.
- **Controls Jobs lists the jobs on the left** and shows the chosen job's schedules and runs on the
  right. Only the Controls nav list scrolls.
- **Focus rings.** The note composer shows one focus ring, matching rich text fields.

### Fixed

- **Field read checks on chained formulas** walked every path again for each formula that read
  another one, so a dozen chained formulas made requests hang. Each answer is now kept per role and
  model.
- **Percent in string formulas.** `compute="price * discount / 100"` with a `Percent` field failed
  at registration. Percent is now a number there
  ([docs/amendment-v2.md](docs/amendment-v2.md)).
- **Virtual values in record responses.** `stored=False` results were missing from
  `GET /api/{model}/{id}`, the create and update responses and `/lines/` pages, so the browser showed
  "Not set". They are now calculated from the saved values, under the same field read checks.
- **Field locks on create.** `locked_when` was judged against the values being created. A new record
  and a new row have nothing locked now; the lock applies from the next write
  ([docs/finite-calculations.md](docs/finite-calculations.md#field-rules)).
- **Money measures in the right currency.** A sum of a Currency field whose currency comes from the
  record was shown in the default currency. The measures and aggregate routes now return
  `currencies`, and the summaries and charts use it
  ([docs/measures.md](docs/measures.md#currencies)).
- **Hourly image purge.** The `evlet_image_purge` task failed on every run. Held images and
  unreferenced private files are now cleaned up hourly.
- **Rich text images in documents.** Saving a document now links the held images its rich text uses
  and deletes the ones it no longer uses. Before, new images were purged after a day.
- **Records with text ids.** Record pages such as `/app/<model>/01hff1w8vgqwf` are served with 200
  instead of 404, and their move and action buttons show again
  ([docs/routing.md](docs/routing.md)).
- **A read-only child table stays read only in the row editor** on a locked or cancelled record, or
  without write access.
- More > Reset layout puts collapsible sections back to their starting state at once.

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
