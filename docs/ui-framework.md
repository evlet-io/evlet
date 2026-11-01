# Evlet UI framework

Status: the production browser interface (phases `ui/01-core` to `ui/03-screens` and
`ui/05-remove-solid` of [handoff-plan.md](handoff-plan.md)). `frontend/index.html` loads it,
`npm run build` bundles it and the Python package serves it. The code lives in
`frontend/src/evlet/`. It has no runtime dependencies; SolidJS and the Vue harness are gone.

| Module | What it does |
|---|---|
| `reactive.ts` | Cells, derives, effects, batching and ownership scopes |
| `dom.ts` | Templates, bindings, `when`, `match`, keyed `each`, `portal`, `mount` |
| `router.ts` | Typed route table over the History API, link handling, guards |
| `api.ts` | Fetch client for the Python routes: session, CSRF, ETags, typed errors |
| `model/record.ts` | Record store: a cell per field, row stores per Table, draft over saved values |
| `model/session.ts` | Calculation session: the finite-v1 engine bound to a record's cells |
| `model/changes.ts` | Save bodies and Table change sets |
| `model/format.ts` | AED money, DD-MM-YYYY dates, GST time |
| `model/finite.ts` | The finite-v1 evaluator, the browser twin of `evlet/calc.py` |
| `model/schema.ts` | Types for `GET /api/schema` |
| `ui/` | Components, their stylesheet and the Library page (see Components) |

There is no virtual DOM. A template builds DOM once, and each dynamic value in it is bound
to one effect that updates one node, attribute or property.

## Reactive core

```ts
import { batch, cell, derive, effect, onCleanup, scope } from './evlet/reactive';

const qty = cell('2', { name: 'invoice.lines[3].qty' });
const rate = cell('12.50', { name: 'invoice.lines[3].rate' });
const net = derive(() => Number(qty.get()) * Number(rate.get()), { name: 'invoice.lines[3].net' });

const stop = scope(() => {
  effect(() => console.log('net is', net.get()));
  onCleanup(() => console.log('stopped'));
});

batch(() => { qty.set('3'); rate.set('10'); }); // the effect runs once, with both values
stop();                                         // effects and derives inside are disposed
```

- `cell(value, {name, equals})` holds a value. `get()` reads and subscribes, `peek()` reads
  without subscribing, `set()` writes. A write that is equal to the current value (by
  `Object.is`, or the `equals` you give) does nothing. `equals: false` always notifies.
- `derive(fn)` is cached and lazy: it computes when read, and again only after something it
  read really changed. Reading a derive in a stale part of the graph first brings its
  sources up to date in dependency order, so it never sees half of a change (glitch free)
  and runs at most once per change, including in diamonds.
- `effect(fn)` runs now and whenever what it read changes. Returning a function, or calling
  `onCleanup`, registers cleanup that runs before the next run and on disposal.
- `batch(fn)` defers effects until the outermost batch ends. A write outside a batch runs
  its effects at once.
- `untrack(fn)` reads without subscribing.
- `scope(fn, {name, onError})` owns everything created inside it and returns `dispose`.
  Disposal runs children newest first, then cleanups newest first, once. Effects created
  inside an effect belong to it and are disposed when it runs again.
- `getOwner()` and `runWithOwner()` attach work done after an `await` to the right scope.
- Ownership rules. An effect or derive created inside a scope or another effect belongs to
  it and is disposed with it. An effect created outside any scope lives until its returned
  stop function is called. A derive created outside any scope subscribes to its sources only
  while a derive or effect reads it; otherwise it remembers the versions it computed from and
  checks them when read, so nothing holds it alive. Effects and derives created in a scope
  that is already disposed never run or subscribe, and `onCleanup` there runs at once.
  A disposed derive keeps its last value; its readers run again.
- Errors are isolated. A throwing effect is reported to the nearest scope's `onError`, or
  to `setErrorHandler`, and the other effects still run. A throwing derive rethrows to its
  readers and recovers when its inputs change.
- An effect that writes a cell it read in the same run runs again until it settles. In
  development, writing a cell while a derive computes throws, naming both. Effects that keep
  re-triggering each other stop after 1,000 rounds; the error names them and goes to the
  nearest `onError`.
- `describe(node)` shows a node's name, value, sources and readers for debugging.

## DOM layer

```ts
import { each, html, mount, when } from './evlet/dom';

const LineRow = (line: () => Line) => html`
  <tr class:locked=${() => line().locked}>
    <td>${() => line().item}</td>
    <td class="num">${() => formatMoney(line().net)}</td>
    <td><button on:click=${() => remove(line())} aria-label="Remove line">Remove</button></td>
  </tr>`;

const dispose = mount(document.querySelector('#app')!, () => html`
  <table>
    <tbody>${each(lines, line => line.id, LineRow, { fallback: () => html`<tr><td>No lines yet. Add the first one.</td></tr>` })}</tbody>
  </table>
  ${when(() => lines.get().length > 20, () => html`<p>${() => lines.get().length} lines</p>`)}`);
```

- `html` templates are parsed once per call site and cloned for each use. `template(markup)`
  does the same for static markup.
- A value in a hole is reactive when it is a function, a cell or a derive; anything else is
  set once. Text goes through Text nodes and attributes through `setAttribute`. Data is never
  parsed as HTML.
- Attribute forms: `name=${v}` (removed when `null` or `false`), `class="row ${kind}"`
  (text around values), `class:name=${on}`, `style:width=${v}`, `prop:value=${v}` (keeps
  the written case), `on:click=${handler}` (removed with the scope) and `ref=${el => ...}`.
- URLs. Values for `href`, `src`, `action`, `formaction`, `xlink:href`, `poster`,
  `background`, `data`, `cite`, `codebase`, `longdesc` and `manifest`, as attributes or as
  `prop:` bindings, pass `safeUrl()`: http, https, mailto and tel URLs and relative or
  fragment URLs are kept; anything else becomes `about:blank`. The check first strips what the
  browser's URL parser ignores (leading and trailing control characters and spaces, tabs and
  newlines anywhere), so `java\tscript:` is caught. `srcset` and `ping` keep only their safe
  URLs.
- Refused when a template is compiled: values in `on...=` attributes (use `on:event`), in
  the `style` attribute (use `style:property=${v}`, which sets one property), in `srcdoc`,
  `innerHTML` and `outerHTML` (as attributes or `prop:`), in `<meta>` `content`, in SVG
  `<animate>` and `<set>` attributes, inside raw-text elements (`<textarea>`, `<title>`,
  `<style>`, `<script>`; bind a textarea with `prop:value=${v}`), in comments, and as tag or
  attribute names.
- Dynamic content always sits between two comment markers, so content added later inside it
  (a `when` inside a `when`, a list that grows) is replaced, moved and removed with it.
- `when(condition, yes, no)` switches only when the truthiness changes and disposes the old
  branch. `match(value, cases, fallback)` does the same per value.
- `each(list, key, render, {fallback})` keeps one row per key. Rows are reused, an item that
  changes for the same key updates the row in place (`render` gets accessors), removed rows
  are disposed, and reordering moves only the rows outside the longest run that kept its
  order. A row that renders one element is that element; any other row (several nodes, text,
  a `when`, an accessor) sits between markers and moves as a unit. A duplicate key is
  reported as an error and leaves the list as it was.
- `portal(target, render)` renders elsewhere (a modal on `document.body`) and removes the
  content with the scope.
- `listen(target, type, handler)` adds a listener that is removed with the scope.

## Router

```ts
import { createRouter } from './evlet/router';

const router = createRouter({ home: '/', list: '/m/:model', record: '/m/:model/:id' });
router.start();
router.href('record', { model: 'invoice', id: '42' }); // '/m/invoice/42'
effect(() => render(router.route.get()));             // {name, params, query, url} or null

router.confirmLeave(() => record.dirty.get(), () => confirmDialog('Discard your changes?'));
```

- Parameters are decoded on match and encoded in `href`. The parameter names are checked
  by TypeScript from the pattern. A parameter of only dots (`.`, `..`) is encoded twice,
  since URL parsers would otherwise treat it as a path step, so every value round-trips.
- Clicks on same-origin links that match a route are handled in the page. Links with a
  modifier key, `target`, `download` or `rel="external"`, and links to paths the table does
  not know (such as `/api/...`), stay ordinary links.
- `guard(fn)` can stop any navigation (sync or async). `confirmLeave` asks only while its
  condition is true and also asks before the tab closes. A refused back or forward step
  returns to the page the user was on. The latest of two concurrent navigations wins.
- `router.signal` is aborted when the route changes. Pass `() => router.signal` to the API
  client so requests of the old page stop.

## API client

```ts
import { ConflictError, createClient, ValidationError } from './evlet/api';

const api = createClient({ signal: () => router.signal });
await api.session();                                   // learns the CSRF token
const { data, etag } = await api.get('order', 1);
await api.update('order', 1, { children: { lines: { update: [{ id: 2, qty: '3' }] } } }, etag);
```

- Writes send `X-Evlet-Request: 1` and `X-CSRF-Token`; the session cookie goes with every
  request. A 401 outside sign-in clears the token, calls `onSessionEnded` and dispatches the
  `evlet-session-ended` window event the current app listens for.
- Record writes take the ETag from the last read and send it as `If-Match`.
- Errors: `ValidationError` (422) with `fields` such as `{"lines.4.qty": "..."}`,
  `ConflictError` for 409, 412 and 428 (decided by the status alone, whatever the body),
  and `ApiError` with a `kind` (`network`, `aborted`,
  `auth`, `forbidden`, `not_found`, `limited`, `server`). `code` holds a leading machine
  code such as `SCHEMA_VERSION_MISMATCH`. `retryable` says whether trying again can help.
- Routes: `session`, `login`, `logout`, `schema`, `list`, `get`, `create`, `update`,
  `remove`, `lines` (`GET /api/{model}/{id}/lines/{table}[/{row id}/{table}...]`),
  `draft` (`POST /api/_draft/{model}`), `action` and `move`
  (`POST /api/{model}/{id}/moves/{to}`).
- `move()` sends no values (the server refuses a move with a body) and the record's ETag.
  It answers with the record, its new `_moves`, `_actions`, `_void`, `_revise` and
  `_effects`, and a new ETag. A move the record does not allow now is a 409
  `ConflictError` whose message gives the reason. Its tests replay responses recorded from
  the real routes, including void, revise, measures, related records and line pages
  (`scripts/generate_ui_framework_fixtures.py`).
- A request's own signal and the page signal are combined with `AbortSignal.any`, or with
  listeners that are removed when the request ends.

## Model runtime

```ts
import { RecordStore } from './evlet/model/record';

const record = new RecordStore({ api, schema, model: 'order' });
await record.open(1);                      // pages in every row, then attaches calculations
const qty = record.table('lines').rows.peek()[0].field('qty');
qty.set('3');                              // amount and total recalculate at once
record.field('total').value.get();         // exact decimal text, for example '305.00'
record.dirty.get();                        // true
await record.save();                       // the server's answer replaces the draft
```

- Each field has `value` (the draft), `server` (the saved value), `dirty`, `error`, `rules`
  (`applies`, `required`, `locked`), `result` (for calculated fields: ready, or pending or
  error with a code), `editable` and `meta` (label, type, scale, choices, `compute`,
  `initial` and the rule expressions from `/api/schema`). Numbers compare exactly, so
  `1.0` is not a change from `1.0000`.
- Each Table is a `Table` with `rows` (a cell of `Row` objects with a stable `key`),
  `add`, `remove`, `move`, `removed`, `total` and `partial`. Rows nest to any depth. Key
  lists by `row.key`: rows keep their objects (and DOM) across saves, including new rows,
  which are matched through the `_row_ids` the server returns.
- When the schema ships a calculation plan, a `FiniteDocument` session
  (`frontend/src/evlet/model/finite.ts`, the same evaluator as the server's fixtures)
  computes calculated fields, `initial` values and the field rules. Results flow into the
  cells; a lock is decided on the record as saved, as the server enforces it. The store
  supplies `today()` as the date in GST and reads Link lookups (`customer.credit_days`)
  with `GET /api/{model}/{id}`, following multi-step paths. Until a lookup arrives, a saved
  result keeps its saved value.
- A detail embeds the first 100 rows of a Table. `open` pages in the rest through the lines
  endpoint before edits are allowed (`ready`). `open(id, {loadAll: false})` skips this for
  read-only views. If paging fails, `loadError` holds the (usually retryable) error, edits
  say why they are refused, and `loadAll()` tries again. A save or action that succeeded
  still returns true when reading its result back fails; `loadError` reports that. Of two
  overlapping `open` calls, the later one wins.
- Writes are refused, with the reason, for calculated, read-only, locked and not-applying
  fields. A value the user cleared on a new record is sent as `null`.
- New rows get keys that are unique in the record and never only digits (digit keys name
  saved rows): a requested `_key` is kept when it qualifies, otherwise `n1`, `n2`...
- A server answer that leaves a field out keeps that field's saved value.
- Lookups that fail (network, permission) leave their results pending and are tried again on
  the next edit; `reload()` reads them again.
- `save()` sends only changes for a saved record: changed fields, and each Table as
  `{create, update, delete, order}` (with `order` only when rows moved), nested for rows of
  rows. A new record sends everything with full row lists. A plan's hash goes along as
  `_calculation`. Conflicts land in `conflict`, field errors on their fields (by row id or
  `_key`, at any depth) and the rest in `formError`. `revert()` and `reload()` discard the
  draft. `runAction()` and `move()` require a clean record. `preview()` asks the server to
  evaluate the draft for plans it does not ship.
- `formatMoney('25200')` is `AED 25,200.00` (exact, half up). Money and numbers take decimal
  strings or safe integers only; a JavaScript fraction or text that is not a plain decimal
  shows `Invalid amount`. `formatDate('2026-11-01')` is `01-11-2026`; impossible dates show
  `Invalid date`. `formatDateTime` shows GST (Asia/Dubai, UTC+4) whatever the browser's
  zone: a timestamp with `Z` or an offset is converted, one without a zone is taken as GST
  already. `todayGst()` is the date `today()` uses.

## Components

`frontend/src/evlet/ui/` holds the component library of the approved design
([ui-design/README.md](ui-design/README.md)). Every component is a plain function that returns
DOM built with `html` and bound to cells. Properties are plain values, accessors or cells; a
plain value is set once, a reactive one updates only the node bound to it. Import
`ui/styles.css` once per page: it carries the design tokens for both themes and the mockup's
class names.

| Module | Components |
|---|---|
| `base.ts` | Icon, IconTile, Button, IconButton, LinkButton, AddChip, Checkbox, Switch, SegmentedControl, Key, Chip, CountBadge, StatusPill, Avatar, RecordMark, Divider, ProgressBar, ScoreBars, EmptyState, InfoNote, StateBanner, Stat, SummaryStrip, FilterChip, SearchInput, QuickAddInput |
| `tooltip.ts` | `tooltip(element, text)`: one page-wide tooltip on hover and focus, never on touch |
| `layers.ts` | `openPopover`, `Menu`, `MenuButton`, `openModal`, `openDrawer`, `toast` (with Undo), `copyText` |
| `fields.ts`, `kinds.ts` | BaseField and one component per field type, `FieldControl`, `displayValue`, `localField`, Link search |
| `inline.ts` | InlineValue, KeyValueList, FormSection |
| `table.ts` | DataTable and the column, resize and virtual-window helpers |
| `childtable.ts` | ChildTable over a record store `Table` |
| `doc.ts` | DocFrame, DocHeader, MoveButton, StatusFlow, Tabs, TabPanel, SideBlock, LockBanner |
| `list.ts` | PageHeader, Toolbar, Filters, ListSummary, BulkBar, Board, BoardCard |
| `library.ts` | The Library page; open `/evlet.html` on the dev server (`npm run dev`) |

Fields bind to the record store's field cells: the value, `error`, the `applies`, `required`
and `locked` rules, and `result` for calculated fields. A field explains the model in place:
a lock hint says why ("Locked when Stage is won"), a calculated value shows its rule
(`ƒ calculated`, "= round(qty * rate, 2)"), an initial value says where it came from
("from product") until the user edits it. Typed text is parsed per type (DD-MM-YYYY dates,
GST date-times, exact decimals, `1h 30m` durations) and a wrong value stays in the input with
a message that says how to fix it. `localField()` makes the same cells for forms that are not
records, such as action dialogs.

A screen composed by hand from a record store:

```ts
import './evlet/ui/styles.css';
import { mount } from './evlet/dom';
import { ChildTable, DocFrame, DocHeader, FormSection, LockBanner, RecordMark, SideBlock,
  KeyValueList, SummaryStrip, TabPanel, Tabs } from './evlet/ui';
import { cell } from './evlet/reactive';
import { formatMoney } from './evlet/model/format';
import { RecordStore } from './evlet/model/record';

const record = new RecordStore({ api, schema, model: 'order' });
await record.open(42);
const tab = cell('details');
const tabs = Tabs({ tabs: [{ id: 'details', label: 'Details' }, { id: 'lines', label: 'Lines', count: () => record.table('lines').rows.get().length }],
  active: tab, label: 'Order', doc: true });

mount(document.querySelector('#app')!, () => DocFrame({
  tint: 'blue',
  main: [
    DocHeader({ type: 'Order', icon: 'receipt', mark: RecordMark({ name: 'Big order', size: 'lg' }), title: record.field('title') }),
    LockBanner({ lifecycle: () => record.field('stage').value.get() === 'won' ? 'locked' : 'draft', state: 'Won' }),
    SummaryStrip([{ label: 'Total', value: () => formatMoney(record.field('total').value.get() as string) }]),
    tabs,
    TabPanel(tabs, 'details', tab, () => FormSection({ title: 'Details', fields: ['title', 'customer', 'stage', 'due', 'total'].map(name => record.field(name)) })),
    TabPanel(tabs, 'lines', tab, () => ChildTable({ table: record.table('lines'), columns: ['product', 'qty', 'rate', 'amount'] })),
  ],
  side: SideBlock({ title: 'Key fields', body: KeyValueList([record.field('stage'), record.field('total')]) }),
}));
```

- **InlineValue** reads first and edits in place: click or Enter opens the field's input,
  Enter saves (one field, through `record.save()`, no Save button), Esc cancels, leaving the
  field saves. Focus returns to the value after Enter or Esc; after Tab or a click elsewhere
  it stays where the user went. Text the user did not change is never written, so a
  date-time keeps its seconds. A refused save keeps the input open with the server's message;
  a conflict says "Someone else changed this record. Reload to see their changes." and offers
  Reload, which reads the record again and keeps the typed value to save again. A saved value
  confirms with a toast that has Undo.
- **ChildTable** binds to a record `Table`: click or Enter edits a cell, Tab and Shift Tab move
  across editable cells and wrap rows, Enter moves down, Esc cancels, arrow keys move between
  cells, typing starts editing, Alt and an arrow moves a row. Rows can be added, duplicated
  (with their rows of rows, as new rows), removed with Undo (`Table.restore()` brings back
  the same rows with their ids and rows of rows; Undo is refused with a message while the
  record saves or the lines are locked), opened in place with every field and their nested
  tables, and the table can take over the view (focus view). Under 480 px the control column
  and the first column get narrower. The footer and the totals under it come from the parent's calculated
  sums over the table (`total = lines.sum("amount")`). Only rows in view are in the DOM.
- **DataTable** sorts (exact for decimals), resizes (double click resets), reorders columns
  (drag a header, or Alt and an arrow), pins the first column, selects rows, moves with the
  arrow keys and opens with Enter. Widths, order and hidden columns live in a `TablePrefs`
  cell the screen saves per user.
- Popovers anchor to their button and close on Esc or an outside click; dialogs focus their
  first field, keep Tab inside, save on Cmd Enter and return focus on close; toasts sit in a
  polite live region that the app creates at mount with `mountToasts()`. A popover stays open
  when a phone keyboard resizes the window while the user types in it. `apiLinks()` reads
  link titles in batches (one list request per target model with `id__in` for the titles a page
  asks for in one turn, none for a record already read or being read), keeps them for the session
  and forgets them when it ends; `recordOf(field)` gives a field's record store. Data is always text: nothing a record holds is parsed as markup.

The Library page (`ui/library.ts`) lists all 114 components of the design catalog with their
details and a live demo for each one built here; the rest are marked for the screens phase.
The development entry (`frontend/evlet.html`, `src/evlet/dev.ts`) runs it against a sample
order replayed from `api-fixture.json`, so record-bound components work without a server. It
is not part of the production build.

### Deviations from the mockup

For the owner to review. Small text must meet WCAG AA (4.5:1, design README section 9), so
these token values differ from `docs/ui-design/reference/styles.css`. Hues are kept; only
lightness moved.

| Token | Theme | Mockup | Now | Contrast now |
|---|---|---|---|---|
| `--fg-3` (secondary text) | light | `#858b99` | `#696f7e` | 5.0:1 on white, 4.6:1 on `--bg-muted` |
| `--fg-4` (icons, faint marks) | light | `#a9aeb9` | `#868d9c` | 3.3:1 on white (non-text, 3:1) |
| `--t-blue` | light | `#3466e6` | `#3365e6` | 4.5:1 on its pill |
| `--t-green` | light | `#159452` | `#128047` | 4.5:1 on its pill |
| `--t-amber` | light | `#c27a0a` | `#9e6308` | 4.5:1 on its pill |
| `--t-rose` | light | `#d6435b` | `#cf2d47` | 4.5:1 on its pill |
| `--t-teal` | light | `#0e8f8a` | `#0c7c78` | 4.5:1 on its pill |
| `--fg-3` | dark | `#7d8494` | `#7e8595` | 4.5:1 on `--bg-muted` |
| `--fg-4` | dark | `#5c6270` | `#626978` | 3.0:1 (non-text) |

Text that used `--fg-4` (field hints, grid placeholders, hidden-column labels, the palette
placeholder, empty quick-access labels) uses `--fg-3` instead.

## Screens

`frontend/src/evlet/app/` is a model-driven app: sign in, Docs, and a list and a record view for
every model the role can read. It needs no per-app frontend code. Everything it shows comes from
`/api/schema`, record details and the list routes, so a model-only application (a `models.py`)
gets all of its screens. The Python server serves it for every UI route. In development run
`npm run dev` and open http://127.0.0.1:5173 (the backend runs on the port in
`EVLET_BACKEND_PORT`, 8000 by default); `/evlet.html` there is the component Library, which is
not part of the build.

| Module | What it does |
|---|---|
| `app.ts` | Session, sign in and first-run setup, the shell and the route table |
| `shell.ts` | Sidebar (models grouped, recent records, Library in development), top bar, keyboard |
| `docs.ts` | The Docs landing page after sign-in |
| `list-page.ts` | List page and board per model |
| `record-page.ts` | Record (doc) view, new records, action, void and revise dialogs |
| `palette.ts` | Command palette (Cmd or Ctrl K) |
| `client.ts` | The API client plus the routes the screens use, and the last detail per record |
| `meta.ts` | What the screens read from the schema, with the optional keys of later phases |
| `prefs.ts` | Per-user preferences |

Routes are the paths the Python server serves (`evlet/ui/routes.py`): `/docs` and
`/docs/:topic`, `/app/:model` (list), `/app/:model/new` (a new record; field values in the query
fill it, `?customer=4`), `/app/:model/:id` (a record; `?tab=` keeps the tab) and the older
`/app/:model/:id/view`, which opens the record. `/settings/accounts` and `/reports/:report` say
that screen is not part of this interface; any other address opens Docs. The earlier
development paths `/ui/...` and `/m/:model` are gone. A list keeps its search, sort, page, view, saved view and filters in the query, so
a link opens the same list. A record with unsaved changes asks before the page changes or the
tab closes. Signing in keeps the address the user opened.

### What the model decides

| Model | Screen |
|---|---|
| Fields the role can read | The only fields shown anywhere; the rest are absent, not hidden |
| `permissions.create`, `update`, `delete` | New buttons and menu entries, editing in place, bulk Delete and the record's Delete |
| `Meta.list_fields` | List columns (up to eight, without `id`); without it the first eight visible fields |
| `Meta.title_field` | The record title (edited in place) and the pinned first list column |
| `Meta.search_fields` | The list search box and record search in the palette |
| `Meta.layout` | Sections of the Details tab; readable fields it leaves out follow under Other |
| `Table(...)` | One tab per table (ChildTable with totals, paging in over 100 rows) and a summary row in Details |
| `compute=`, `initial=`, `applies_when`, `required_when`, `locked_when` | Calculated hints with the rule, initial-value hints, hidden, required and locked fields with the reason |
| `@action` (arguments or `inputs`) | Header buttons; an action with inputs opens a dialog that checks them first. Form dialog actions (`dialog: true`) are not shown: these screens cannot open them yet |
| `State` with moves (Phase 4) | Saved view tabs per state with counts from `GET /api/{model}/aggregate` (with or without declared measures), a board, the status flow, move buttons; blocked moves stay visible and say why (`_moves`). With a state tab chosen, the board shows that one column |
| Locked states, `editable_when_locked` | Lock banner; only the declared fields stay editable; lines lock |
| Void and revise (Phase 5) | Dialogs with a required reason; banners and links both ways (`revises`, `revised_by`) |
| `Sequence` (Phase 7) | The number in mono as the title; "Number assigned on ..." until then |
| `Meta.measures` (Phase 9) | Model-level measures in a strip over the list (following its filters), board column counts and totals of the first declared sum measure (from the aggregate route, with the measure's `where`, as exact amounts); per-record measures in the record's summary strip |
| `Meta.key_fields` (Phase 10) | Header facts, the summary strip and the Key fields side block (without it: the first fields) |
| `Meta.relations` (Phase 10) | The Related tab and side block |
| `attachments` | The Files side block, with upload for roles that can update while the record is not locked |
| `Effects` (Phase 6) | The Postings tab: what was posted, reversals and the balance check |
| Journal (Phase 8) | The Activity tab, first: entries by day with actors named from the page's `users` (the engine as "The system"), field changes old and new, Table rows counted, moves, numbers, postings and their balance, files, messages, calls and follow ups with their due date, assignee and done state; entries marked `partial` say some details are hidden. The note box shows only when the schema's `journal.kinds` lets the role post a note |

A section appears only when the schema or the detail carries its key. The screen tests replay
these keys as the engine sends them, recorded from an invoice with a State, a Sequence, effects,
measures, key fields, relations, files and a journal (engine and user entries, follow ups, and a
viewer who sees the posting entry only in part). A record that has posted nothing yet still lists each
effect target with zero rows; the Postings tab treats that as nothing posted. The files of a
locked record cannot change, so the upload control hides while `_locked` is true. Every response
is recorded by `scripts/generate_ui_framework_fixtures.py`; none is written by hand.

Model icons and accents come from the model name (invoice: receipt, blue; contact: people,
violet), since the schema has no presentation keys. Models with a State or a Table are listed as
Documents in the sidebar, the others as Records.

Per-user preferences (theme, sidebar, density, list column widths, child table columns, quick
access) are kept in browser storage under `evlet.<username>.` until the backend has user
settings. They never hold record data: recent records and recent searches stay in memory for the
signed-in session and go on sign-out. The sign-in screen uses the default theme.

Signing out ends the local session only after the server confirms it (or answers that the
session had already ended). If the request fails, the user stays signed in and a dialog says so,
with Try again.

A create link (`/app/{model}/new?field=value`) fills only fields the form shows and the role can
write: hidden, read-only (including fields the role cannot write), calculated, State and
Sequence fields are ignored.

Bulk moves and deletes read each record first and act only when the values the list showed
(its columns and State) are unchanged; records that changed since the list loaded are left as
they are and counted in the result. They send four requests at a time, show progress, and stop
when the user leaves the page.

## Tests and benchmarks

The tests in `frontend/src/evlet/tests/` run in jsdom with `npm test`. API and record store
tests replay responses recorded from the real ASGI routes in `api-fixture.json`, and the
screen tests also those in `screens-fixture.json` (regenerate both with
`.venv/bin/python -m scripts.generate_ui_framework_fixtures` and review the
diff; `tests/test_ui_contracts.py` records both again and compares). The finite engine and the
record store replay the shared finite-v1 scenarios (`finite-fixtures.json`).
`npm run bench:ui` measures the budgets; see "UI framework" in
[performance.md](performance.md).

## Limits of this phase

- Home, accounts, reports, form dialogs, the notification panel, the layout control and Doc
  layout page are not built. Python pages (`evlet.ui` PageView), native panels and custom
  components are not rendered (owner decision); their Python APIs remain.
- Bulk moves and deletes read each record first, one request per record.
- The board loads the first 100 records of the filter. Column counts and totals come from the
  server; the aggregate route has no search, so while searching columns count the loaded cards
  and show no totals.
- Postings show what each target received (rows, reversals, balance); listing the posted rows
  waits for a filter by source on the target's list route.
- Date fields take typed DD-MM-YYYY text; there is no calendar picker yet. Reference fields
  are read only until the schema lists the models they may point to.
- State metadata (`record.state`) is read when the schema sends it.
- Lookups that a role cannot read stay pending; their saved results keep showing.
