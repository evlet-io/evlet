# Evlet UI framework

Status: the browser interface Evlet ships (phases `ui-final/01-core` to `ui-final/03-screens`
and `ui-final/05-remove-solid` of [handoff-plan.md](internal/handoff-plan.md)). `frontend/index.html` loads it,
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
| `richtext/` | The rich text editor: model, operations, HTML reading and writing, rendering, undo ([Rich text](#rich-text)) |

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
  condition is true and also asks before the tab closes. A navigation that changes only the
  query, such as switching record tabs (`?tab=`), stays on the page and never asks. A refused back or forward step
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
  rows. A new record sends everything with full row lists. A collection whose schema entry has
  `row_changes: false` (a `ChildTable` on a plain model) is always sent as a full list:
  saved rows as `{id, ...changed values}`, new rows without `_key`. A plan's hash goes along as
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

The fonts are bundled, so pages load nothing from another host and the CSP stays
`default-src 'self'`. `frontend/fonts` holds Inter 4.1 (`InterVariable.woff2` and
`InterVariable-Italic.woff2`, weights 100 to 900) and JetBrains Mono 2.304
(`JetBrainsMono-Regular.woff2` and `-Medium.woff2`), taken from the official GitHub releases
(`rsms/inter`, `JetBrains/JetBrainsMono`), with their SIL Open Font License 1.1 files beside them.
The Inter files are byte for byte the ones evlet.io serves, and the family name
(`"Inter Variable"`) and fallback stack match the site's brand tokens. The build copies them to
`/assets` with a content hash (926,204 bytes of woff2 in all); the server sends them as
`font/woff2`. `font-display: swap` shows the system fonts (`system-ui`, `ui-monospace` and the
rest of the stack) until they load. The italic file downloads only when italic text shows.

The Evlet logo is the evlet.io artwork as the site serves it, in `frontend/public/assets`:
`evlet-logo-primary.svg` (the horizontal logo), `evlet-icon-blue.svg` (the folded leaf mark, also
the favicon) and `evlet-logo-white.svg`, the primary logo with its wordmark filled white for dark
themes (the site publishes no dark variant; only the fill changes). `Lockup()` and `Logo()` in
`app/login.ts` place them in the sidebar, sign-in and Controls; the collapsed sidebar shows the
mark only.

| Module | Components |
|---|---|
| `base.ts` | Icon, IconTile, Button, IconButton, LinkButton, AddChip, Checkbox, Switch, SegmentedControl, Key, Chip, CountBadge, StatusPill, Avatar, RecordMark, Divider, ProgressBar, ScoreBars, EmptyState, InfoNote, StateBanner, Stat, SummaryStrip, FilterChip, SearchInput, QuickAddInput |
| `tooltip.ts` | `tooltip(element, text)`: one page-wide tooltip on hover and focus, never on touch |
| `layers.ts` | `openPopover`, `Menu`, `MenuButton`, `openModal`, `openDrawer`, `toast` (with Undo), `copyText` |
| `fields.ts`, `kinds.ts` | BaseField and one component per field type (RichTextField and RichTextView for rich text), `FieldControl`, `displayValue`, `localField`, Link search, the Select control, `currencyFor` and `currencyIn` |
| `pickers.ts` | Dropdown, ChoiceList, Calendar, Stepper, TimeSteppers, DatePanel, DateTimePanel, TimePanel, DurationPanel, DateRangePanel, `rangePresets`, `rangeText` |
| `inline.ts` | InlineValue, KeyValueList, FormSection |
| `fold.ts` | FoldSection, Folds, `foldSummary`, `revealField`, `revealFirstError` ([collapsible sections](ui.md#collapsible-sections)), `flowGrid` (rows or columns) |
| `draglist.ts` | `dragList(root, { kind, move, label, zoneLabel })`: reorder by mouse, touch or keyboard, with announcements |
| `table.ts` | DataTable and the column, resize and virtual-window helpers |
| `childtable.ts` | ChildTable over a record store `Table`, and ChildRows for a narrow column |
| `split.ts` | SplitView: a main area and a resizable side panel |
| `doc.ts` | DocFrame (page or panel variant), DocHeader, MoveButton, StatusBadge, StatusFlow, Tabs, TabPanel, SideBlock, LockBanner |
| `list.ts` | PageHeader, Toolbar, Filters, ListSummary, BulkBar, Board, BoardCard |
| `theme.ts` | `applyTheme`, `resolveTheme`, `systemScheme`, `currentTheme`, `THEMES`, `isTheme` ([Themes](#themes)) |
| `time.ts` | `RelativeTime`, `relativeTime`, `exactTime`, `zoneLabel`, `RELATIVE_STEPS`, `minuteClock` ([Relative times](#relative-times)) |
| `../app/filters.ts` | The list filter builder: filterBuilder, FilterPanel, FilterChips, filterFields, conditionsFor, encode, decode, compact |
| `library.ts` | The Library page; open `/evlet.html` on the dev server (`npm run dev`) |

Fields bind to the record store's field cells: the value, `error`, the `applies`, `required`
and `locked` rules, and `result` for calculated fields. A field explains the model in place:
a lock hint says why ("Locked when Stage is won"), a calculated value shows its rule
(`ƒ calculated`, "= round(qty * rate, 2)"), an initial value says where it came from
("from product") until the user edits it. Typed text is parsed per type (DD-MM-YYYY dates,
GST date-times, exact decimals, `1h 30m` durations) and a wrong value stays in the input with
a message that says how to fix it. `localField()` makes the same cells for forms that are not
records, such as action dialogs.

### Rich text

A `RichText` field (`type: "richtext"` in the schema) edits in `RichTextEditor`
(`frontend/src/evlet/richtext/editor.ts`), Evlet's own editor with no dependencies. The design is in
[internal/rich-text.md](internal/rich-text.md).

```ts
import { RichTextEditor } from './evlet/richtext/editor';

const brief = cell<string | null>('<p>Fit out the <strong>Dubai</strong> office.</p>');
const editor = RichTextEditor({ value: () => brief.get(), onChange: html => brief.set(html), label: 'Brief', maxLength: 20000 });
document.body.append(editor.element);
```

- The document is a flat list of blocks (paragraph, heading 2 and 3, bulleted, numbered and check
  list items nested up to three levels, quote, code block, divider) with marks (bold, italic,
  underline, strike, inline code) and links (http, https, mailto) on its text. `onChange` gets the
  canonical HTML, the same the server stores, or null for an empty document.
- The surface is `contenteditable` with `role="textbox"` and `aria-multiline`, but the browser never
  edits it on its own: each `beforeinput` is cancelled and applied to the model, which renders.
  During IME composition, and for the input phone keyboards send without letting it be cancelled,
  the browser edits the text node and the editor reads the changed block back, keeping its marks.
  Spellcheck and autocorrect replacements arrive as `insertReplacementText` or through that path.
- Undo and redo are the editor's own: a burst of typing (until a pause of a second, a new word, a
  caret move or another kind of change) is one step, up to 200 steps.
- Keys: Ctrl or Cmd with B, I, U (marks) and K (link), Shift+Ctrl+7 numbered and Shift+Ctrl+8
  bulleted list, Ctrl+Z and Ctrl+Shift+Z or Ctrl+Y. Tab and Shift+Tab indent inside lists only;
  elsewhere Tab leaves the editor. Ctrl or Cmd Enter is left to the form (an InlineValue saves).
  Typing `# `, `## `, `### `, `- `, `1. `, `> `, `[] `, `[x] `, three backticks or `---` at the
  start of a paragraph formats it, and text between backticks becomes inline code; Backspace or
  Undo right after gives the typed characters back.
- Paste keeps the schema only: Word and Outlook (list paragraphs become lists), Google Docs (its
  bold wrapper is ignored, styled spans become marks, `aria-level` nests lists) and web pages.
  Styles, classes, scripts, frames, images and unsafe links never survive. Plain text keeps
  paragraphs and lines, and web addresses become links. A web address pasted over selected text
  links it. Copy writes canonical HTML and plain text.
- The toolbar is one Tab stop (arrow keys, Home and End move; disabled buttons are skipped) and
  shows what is on with `aria-pressed`. On desktop a small toolbar floats over selected text; on a
  phone the toolbar sits above the on-screen keyboard. Formatting changes are announced in a polite
  live region ("Bold on", "Bulleted list").
- `RichTextView(html)` (`ui/fields.ts`) shows stored rich text read only, with nested lists and real
  links. Stored HTML is parsed with `DOMParser` and rebuilt from the model, never assigned as markup.
- The server is the security boundary: it sanitises every write ([model-engine.md](model-engine.md)).
- Images (`images: ImageSource`, given by fields on a record through `recordImages()` in
  `richtext/images.ts`): the Image button, a pasted or dropped image file, or an image copied from
  another record's text go in at the caret as a placeholder with a progress bar. The browser
  refuses SVG and anything but PNG, JPEG, GIF and WebP, shrinks the image to 2,000 px on the long side
  and re-encodes it (WebP when it can, else JPEG, or PNG to keep transparency; a GIF that fits is
  kept, so it stays animated), then uploads it to the record, or to the person's holding area before
  the record is saved. Only the attachment id is stored. Each image has tools for small, medium and
  full width, alt text and caption, and remove. Copy marks images with their record, so a paste into
  the same record keeps the reference and a paste into another record uploads the image again. An
  image that can no longer be loaded says so. Undo after an upload brings back the image itself.

### Relative times

`RelativeTime({ at, exact?, onToggle?, className? })` shows a moment as words relative to now, in a
`<time datetime="...">` element. Hover or keyboard focus shows the exact time in a tooltip, in the
person's date format and zone: `04-11-2026 14:05 GST` (GST unless they chose another zone in
Settings). Its accessible name holds both forms.

| Time since | Words |
|---|---|
| under 1 minute | just now |
| under 1 hour | 1 minute ago, 2 minutes ago ... |
| under 24 hours | 1 hour ago, 2 hours ago ... |
| under 7 days | yesterday (24 to 48 hours), 2 days ago ... |
| under 5 weeks | last week (7 to 13 days), 2 weeks ago ... |
| 5 weeks or more | the date, such as 30-09-2026 |

The thresholds live in one list, `RELATIVE_STEPS`, and the words come from `Intl.RelativeTimeFormat`
with no library. A time slightly in the future (another clock) reads "just now".

All RelativeTimes on a page share one minute clock (`minuteClock()`): one timer while any is shown,
none while the tab is hidden, and a catch-up the moment it shows again, so "just now" becomes "1 minute
ago" by itself. With `exact` the element shows the exact time and the tooltip the words; with
`onToggle` it is a button (`aria-pressed`) that switches them.

The record page's Record info block shows **Created** and **Last modified** (and Revises and Revised by
when they apply); the record's own id is copied from More > Copy record ID. It shows the times from the detail's
`_audit` ([model-engine.md](model-engine.md#record-times)): `2 hours ago · Aslam`. Clicking a
time switches every time in Record info between relative and exact, remembered per person
(`prefs.exactTimes`). Who is the person's display name, a link to Controls Users for people who may
open Controls and plain text otherwise; it is absent for engine writes and for portal roles. The
Activity tab's entry times use RelativeTime too, without the toggle.

### Typing dates and times

Date, date-time, time and duration inputs read typed text the forgiving way Frappe does, through
one pure module, `ui/dateparse.ts` (`parseDay`, `parseClock`, `parseDayTime`, `parseLength`). The
text is read on blur and on Enter, and the input then shows the value in the person's format at
once. Text that can't be read, or names a day that doesn't exist (31-02), changes nothing: the
field keeps its value and the input shows it again. The field's error says what was typed,
"Couldn't read 1.62 as a duration", and goes with the next edit. Nothing is guessed.

These forms are a browser convenience only. The API takes the stored forms and nothing else:
`YYYY-MM-DD` dates, ISO date-times with a time zone, `HH:MM` or `HH:MM:SS` times, and ISO 8601
durations (`PT1H30M`) or whole seconds. Anything else sent to it directly (`2027-02-31`, `120627`,
`25:00`, `9am`, `1.62`, `1.5`, `1h 30m`) is a validation error, never coerced.

| Typed (day first) | Reads as (today 02-11-2026) |
| --- | --- |
| `12062027`, `120627`, `12/06/27`, `12-06-2027`, `12.06.2027`, `12 06 2027`, `12 Jun 2027` | 12-06-2027 |
| `1/6/27` | 01-06-2027 |
| `1206`, `12/06` | 12-06-2026 (this year) |
| `12` | 12-11-2026 (this month) |
| `today` or `t`, `tomorrow`, `yesterday`, `+3`, `-3` | days from today |
| `2027-06-12` | always year first |

The order of day, month and year follows the person's date format in Settings (Appearance &
region): `MM/dd/yyyy` reads month first (`061227` is 12 June), `yyyy-MM-dd` reads year first,
and the other formats day first. No separate setting is needed. A two-digit year is the year
ending in those digits that is nearest this year: up to 50 years ahead, otherwise in the past
(in 2026, `76` is 2076 and `77` is 1977).

Times: `1235` is 12:35, `120627` 12:06:27, `935` 09:35, `9` 09:00; `12:35`, `12.35`, `9am`,
`12.35pm`, `12:35 pm`, `2:05 p.m.`. `12am` is 00:00 and `12pm` 12:00. Minutes always have two
digits, so `9.5` is refused rather than guessed. A date-time is a date, then a time after a space,
a comma or `T` (`120627 1235`, `12/06/27 9am`, `today 14:30`); a date alone is midnight.
Durations:

- A whole number without a unit is minutes: `90` is 1h 30m.
- `h:mm` or `h:mm:ss` is hours, minutes and seconds, with a colon or a dot: `1:30`, `1.30`
  (1h 30m), `1.05` (1h 5m), `1:30:20`, `1.30.20`. A dot without a unit must be followed by exactly
  two digits from 00 to 59, so `1.5`, `1.62`, `1.300` and `1.5.` are refused.
- With units, spaces and words are fine: `1h 30m`, `1h30m`, `1 h 30 min`, `1hr 30mins`, `2 hours`,
  `45 sec`, `2d 4h`. A number with a unit may be a decimal fraction of that unit: `1.5h` is 1h 30m,
  `1.30h` is 1h 18m, `1.62h` is 1h 37m 12s, `1.5m` is 1m 30s. A total that is not a whole number of
  seconds is refused.
The date range filter's From and To boxes read days the same way.

### Form fields

BaseField follows the Untitled UI input structure in Evlet's colours. Inputs, Link and Select
buttons, unit boxes and tag fields are 40 px tall with 14 px text and 14 px side padding, so a
form row lines up. At rest an input has a 1 px light grey border and a faint shadow; on focus
a lighter blue border and a soft 4 px blue ring; with an error the same in rose. The label is
14 px, weight 500, 6 px above the input, and the required `*` is the accent blue (its
`required_when` tooltip stays). `help_text` reads under the input in grey and is linked with
`aria-describedby`; an error replaces it while there is one. A field with `tooltip` gets a ? icon
at the right end of the input (before a Select's or Link's chevron, between the input and a unit
box) that shows the text on hover and focus. Compact places keep their sizes: the record side
panel (`.fields`), child table cells, list filters and quick add. Inter is the font, as before.

### Pickers

No field uses a browser control for picking (no native `<select>`, no `type="date"`). The
pickers open in a popover (`openPopover`), close on Esc or a click outside and give focus back
to the field. Every value they pick goes through the same parsing as typed text, and the server
checks it again.

- **Select** is a `Dropdown`: a button that shows the choice (or "Choose one"). Settings and
  Controls use the same `Dropdown`, so no page shows the browser's `<select>`. Click, Down or Up opens the
  list with the current choice ticked (`aria-selected`). Up and Down move, Home and End jump,
  Enter or Space picks, a letter jumps to the next choice that starts with it. From 8 choices
  the list has a search box. Clear shows when the field is optional and set. A retired choice is
  listed, disabled, only on the record that still holds it. Typing a letter on the closed
  button opens the list at it.
- **Date** keeps its DD-MM-YYYY input (or the person's date format) and adds a calendar button.
  Clicking the input or the button opens a month grid (weeks start on Monday, today is marked);
  Down or Alt+Down opens it and moves focus to the chosen day. Arrows move by day and week, Page
  Up and Page Down by month (with Shift by year), Home and End to the week's ends, Enter picks.
  The title opens a month grid, and its title a 12-year grid. Today and Clear (optional fields)
  sit below. Picking a day writes it and closes; in an InlineValue it saves at once, like Enter.
- **Date-time** adds hour and minute steppers (`role="spinbutton"`; arrows move by 1, the
  buttons move minutes by 5) and Now, Clear and Done. Times are the person's zone (GST by
  default); the value is stored in UTC.
- **Time** has the steppers, common times (09:00, 12:00, 13:00, 17:00), Now, Clear and Done.
- **Duration** has hour and minute steppers and common lengths (15m to 8h).
- **Date range** (`DateRangePanel`) offers presets (Today, Yesterday, This week, Last 7 days,
  This month, Last month, Last 30 days, This quarter, This year, Last year) or a first and last
  day on the calendar, in either order, with the days between shaded. Apply reports
  `[first, last]`.
- **Grouped search** (`GroupedChoiceList`, and `GroupedPicker`, the button that opens it) searches a
  long list of choices in groups, such as a model's fields and then each child table's: every
  word typed must appear in the group, label or value. It draws at most `limit` (100) matches,
  says how many more there are, and picks with arrows and Enter. The list filter builder uses it
  for the field of each row ([ui.md](ui.md#filters)).

### Side boxes

A Currency field shows its code in a box before the input, a Percent field `%` after it, and an
Integer or Decimal its `suffix` after it (and after the value at rest: `5.00%`, `8 hours`). The
currency code comes from the schema: `currency` when it is fixed (on the field, or an app
setting the server read), or `currency_source` when it lives on the record (`{"field"}`) or on a
linked record (`{"link", "field", "model"}`). `currencyFor(field)` follows the source as it
changes, so picking another customer changes the box from AED to USD. Linked records are read
through `LinkSource.record()`, batched with Link titles (one list request per model). Lists use
`currencyIn(meta, value)` with the row's values. An empty or unreadable source shows AED.

The Link field's record mark is the 20 px `org-mark`; it never stretches.

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
  across editable cells and wrap rows (from a cell being edited or not; past the last cell Tab
  goes on to Add row), Enter moves down, Esc cancels, arrow keys move between cells. One cell is
  active at a time and it holds the focus (a roving `tabindex` in a `role="grid"`): a click or tap
  focuses the clicked cell, a cell that can't be committed keeps the focus, and an unchanged cell
  never blocks moving on. A choice from a dropdown, Link search or picker commits the cell and
  returns the focus to it, so Tab goes to the next cell and Enter to the cell below. Typing starts
  editing (on a Link cell it opens the search with what was typed), Alt and an
  arrow moves a row. Rows can be added, duplicated
  (with their rows of rows, as new rows), removed with Undo (`Table.restore()` brings back
  the same rows with their ids and rows of rows; Undo is refused with a message while the
  record saves or the lines are locked), opened in place with every field and their nested
  tables, and the table can take over the view (focus view). With `heading` (the record's type,
  icon, mark, title, number and a `footer` such as the form's save bar), focus view keeps a compact
  record header above the table and the save bar below it, outside the rows that scroll; on a phone
  the header is one line. Esc or Back to form returns to the form. Under 480 px the control column
  and the first column get narrower. The footer and the totals under it come from the parent's calculated
  sums over the table (`total = lines.sum("amount")`). Only rows in view are in the DOM.
- **DataTable** sorts (exact for decimals), resizes (double click resets), reorders columns
  (drag a header, or Alt and an arrow, within the pinned or the scrolling range), pins columns on
  the left (`pinned`, default 1, or the saved range), selects rows, moves with the arrow keys and
  opens with Enter. Widths, order, hidden columns and the pinned count (`pinned`) live in a
  `TablePrefs` cell the screen saves per user; prefs saved without `pinned` keep the table's
  default.
- **ColumnsMenu** is the Columns menu of ChildTable and the report page: show or hide each column
  (one stays shown), drag a grip to reorder (mouse, touch, or the keyboard: Space lifts, the arrow
  keys move, Space or Enter drops, Esc cancels, announced as "Phone, position 3 of 5"), and a
  Pinned range at the top whose divider can be dragged too. The pin button pins a column at the end
  of the range or unpins it to the start of the rest. One column always stays unpinned, and the
  menu warns, without blocking, when pinned columns take more than 60% of the table's width.
  Every pinned column is sticky, with its own left offset, in the header, cells and totals. The
  dragging is `dragList` (`ui/draglist.ts`), with the divider as one more item in the list. When the frame is wider than the columns, the last column
  takes the spare width, so the header and row rules reach the edge; the child table does the same.
  `current` marks the row open beside the table (`aria-current`) and keeps it in view; `onMove`
  reports the row the arrow keys move to, so a side panel can follow.
- **SplitView** puts a side panel beside a main area. The divider is a `separator`: drag it, move
  it with Left and Right (Home and End for the widest and narrowest), double click to reset. The
  width is a cell the caller keeps; the panel takes at least `minSide` (380 px) and at most 70
  percent, leaving `minMain` (320 px). Below `breakpoint` (900 px) the panel covers the main area
  and `overlay` says so.
- **ChildRows** shows a record `Table` in a narrow column: one line per row with its first value
  and its last amount, the other numbers under it; a row opens in place with its fields, and Add
  row, Remove row and Open full table sit around it. Locked lines show values only.
- **FormSection** with `fold` is a **FoldSection**: a heading button with `aria-expanded`, `aria-controls` and a chevron, a summary and an error
  count while closed, and `hidden` on the closed body so Tab skips it. A `Folds` holds one form's state: the
  person's choices (a preferences cell of title to open) over each section's start (`collapsed`, the phone rule,
  `'empty'`); `reveal(title)` opens one for now and `reset()` forgets the choices. `revealField(root, name)`
  opens the section holding a field and focuses it. `flowGrid(cells, flow)` lays a section's fields out
  across then down, or down each column with full-width fields as breaks, in DOM order.
- **dragList** reorders items in one or more lists. Mark items `data-drag-item="<kind>"` with
  `data-drag-id`, the lists they may sit in `data-drop-zone="<kind>"` with `data-zone`, and give each item a
  handle button `data-drag-handle="<kind>"`. Pointer (mouse, pen, touch): press a handle and move past
  4 px; the item under the pointer shows where it lands. Keyboard: Space or Enter lifts, arrows move
  (across list edges), Space or Enter drops, Escape puts it back. Every step is announced in a polite
  live region. Listeners sit on `root`, so the lists may be redrawn after each `move`.
- **enterMovesToNextField(root, on)** makes Enter and Shift+Enter move between the `[data-field]` blocks
  under `root` while `on()` is true.
- Popovers anchor to their button and close on Esc or an outside click; dialogs focus their
  first field, keep Tab inside, save on Cmd Enter and return focus on close; toasts sit in a
  polite live region that the app creates at mount with `mountToasts()`. A popover stays open
  when a phone keyboard resizes the window while the user types in it. `apiLinks()` reads
  link titles in batches (one list request per target model with `id__in` for the titles a page
  asks for in one turn, none for a record already read or being read), keeps them for the session
  and forgets them when it ends; `recordOf(field)` gives a field's record store. Data is always text: nothing a record holds is parsed as markup.

The Library page (`ui/library.ts`) lists all 117 components of the design catalog with their
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
| `--fg-3` (secondary text) | light | `#858b99` | `#676d7c` | 5.2:1 on white, 4.6:1 on `--accent-soft` |
| `--fg-4` (icons, faint marks) | light | `#a9aeb9` | `#868d9c` | 3.3:1 on white (non-text, 3:1) |
| `--t-blue` | light | `#3466e6` | `#3365e6` | 4.5:1 on its pill |
| `--t-green` | light | `#159452` | `#128047` | 4.5:1 on its pill |
| `--t-amber` | light | `#c27a0a` | `#9e6308` | 4.5:1 on its pill |
| `--t-rose` | light | `#d6435b` | `#cf2d47` | 4.5:1 on its pill |
| `--t-teal` | light | `#0e8f8a` | `#0c7c78` | 4.5:1 on its pill |
| `--fg-3` | dark | `#7d8494` | `#868d9d` | 5.0:1 on `--bg-muted`, 4.6:1 on `--accent-soft` |
| `--accent` (links, marks) | dark | `#5b80f5` | `#6488f6` | 4.7:1 on `--accent-soft` |
| `--accent-hover` | dark | `#6d8ef7` | `#7593f7` | follows `--accent` |
| `--fg-4` | dark | `#5c6270` | `#626978` | 3.0:1 (non-text) |

Text that used `--fg-4` (field hints, grid placeholders, hidden-column labels, the palette
placeholder, empty quick-access labels) uses `--fg-3` instead.

White text on the mockup's dark `--accent` is 3.6:1, so filled accent surfaces (primary
buttons, ticked checkboxes, the current step, chosen calendar days, badges) use
`--accent-solid` instead; text on a solid `--t-*` fill (Success, Delete, a document's current
step) uses `--on-tint`, which is dark in the dark theme.

### Themes

Light, dark and system ("match my device") are a person's Settings choice, kept on the server
([settings.md](settings.md)). All colours are custom properties on `:root` in `ui/styles.css`:
light values on `:root`, dark values under `@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])`, and the same dark values again under
`:root[data-theme="dark"]`, so an explicit choice wins in both directions and "system" needs
no script. Each block also sets `color-scheme`, so scrollbars and native controls match.

| Token group | Use |
|---|---|
| `--bg`, `--bg-subtle`, `--bg-muted`, `--side-bg`, `--surface`, `--overlay` | Grounds, cards, popovers and the dialog scrim |
| `--border`, `--border-strong`, `--divider` | Lines |
| `--fg`, `--fg-2`, `--fg-3`, `--fg-4` | Text from strongest to faint; `--fg-4` is for icons and marks only |
| `--accent`, `--accent-hover`, `--accent-soft`, `--accent-ring` | Links, active marks, selected rows, focus rings |
| `--accent-solid`, `--accent-solid-hover`, `--accent-fg` | A filled accent surface and the text on it |
| `--t-{blue,green,amber,rose,violet,teal,slate}` and `-bg` | Tints: icons, pills, charts, validation (`--t-rose`) |
| `--on-tint` | Text on a solid `--t-*` fill |
| `--inverse-bg`, `--inverse-fg`, `--inverse-ok`, `--inverse-hover` | Toasts, tooltips, the bulk bar and the Settings save bar (dark in light, light in dark) |
| `--focus-border`, `--focus-ring`, `--error-ring`, `--knob`, `--shadow-*` | Focus, the switch knob, shadows, sticky column edges |
| `--swatch-*` | The theme pictures in Settings (the same in both themes) |

Components and screens use tokens only; `color-mix()` over a token is fine for a translucent
shade. `tests/theme.test.ts` fails on any colour literal (hex, `rgb()`, `hsl()`, named white or
black) in a stylesheet outside the token blocks or in an inline style, checks that both dark
blocks are the same and that every colour token has a dark value, and checks WCAG AA contrast of
the token pairs in both themes: 4.5:1 for text (each text token on each ground, the accent on its
soft ground, text on solid fills, each tint on its pill) and 3:1 for icons, the focus outline and
error lines. Add a pair there when a new token carries text.

`ui/theme.ts` is the runtime API:

```ts
import { applyTheme, resolveTheme, systemScheme, currentTheme } from './evlet/ui';

applyTheme('dark');              // data-theme="dark" on <html>, browser bar colour, evlet_theme cookie
applyTheme('system');            // removes data-theme: the stylesheet follows the device
systemScheme().get();            // 'light' | 'dark', a cell updated when the OS setting changes
resolveTheme('system');          // what shows now: 'light' or 'dark'
currentTheme();                  // the theme the page was served with
```

No flash on load: the server writes `data-theme` on `<html>` (and `"theme"` in the config
block) for the signed-in person before any script runs, and the stylesheet is in `<head>`. The
sign-in page reads the `evlet_theme` cookie that `applyTheme` leaves, so it opens in the
browser's last theme. `index.html` also carries `<meta name="color-scheme" content="light dark">`
and a `theme-color` for each scheme. The Vite development server has no config block, so there
the page follows the device until the person's settings load.

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
| `shell.ts` | Sidebar (models grouped, Library in development, the account and the theme button), top bar, keyboard |
| `recents.ts` | The history button left of search in the top bar: this session's recent records, newest first, with the model and when each was opened |
| `docs.ts` | The Docs landing page after sign-in |
| `list-page.ts` | List page and board per model |
| `record-page.ts` | Record (doc) view, new records, action, void, revise, unpost and repost dialogs |
| `slips.ts` | Slips ([slips.md](slips.md)): `presentSlip()`, the slip modal and what follows a hand-in |
| `report-page.ts` | Reports ([reports.md](reports.md)): the index, one report's filters, rows, totals, chart, Export and Print |
| `palette.ts` | Command palette (Cmd or Ctrl K) |
| `client.ts` | The API client plus the routes the screens use, and the last detail per record |
| `meta.ts` | What the screens read from the schema, with the optional keys of later phases |
| `prefs.ts` | Per-user preferences |
| `config.ts` | Reads the runtime config block the server puts in every page |

Every page the server serves carries one non-executable block in `<head>`:
`<script id="evlet-config" type="application/json">{"frontend":"evlet.desk","base":"/app","api":"/api",...,"theme":"dark"}</script>`.
The router reads `base` (where the model screens are mounted) and `docs` from it, and the API
client sends `/api/...` paths to `api`, so the same build runs under any mount path. Nothing
in the app hardcodes `/app` or a host; a page without the block (the Vite server) uses the desk
values.

Routes are the paths the Python server serves (`evlet/ui/routes.py`): `/docs` and
`/docs/:topic`, `/app/:model` (list), `/app/:model/new` (a new record; field values in the query
fill it, `?customer=4`), `/app/:model/:id` (a record; `?tab=` keeps the tab) and the older
`/app/:model/:id/view`, which opens the record. A list with a record in its side panel keeps
it in `?open=` (the id, or `new`) and the record's tab in `?rtab=`. `/reports` and `/reports/:report` are reports
([reports.md](reports.md)); `/controls` loads Controls as its own chunk (`frontend/src/evlet/controls/`,
see [controls.md](controls.md)); any other address opens Docs. The earlier
development paths `/ui/...` and `/m/:model` are gone. A list keeps its search, sort, page, view, tab and filters in the query, so
a link opens the same list. Without `?tab=` it opens on the model's default tab (`Meta.list_tab`); choosing
another, All included, puts it in the query. A record with unsaved changes asks before the page changes or the
tab closes. Signing in keeps the address the user opened.

### Side panel

A list opens a record on its own page or in a side panel beside it. `Meta.open_in = "panel"`
sets the app's default, the Controls list view ("Open records in") changes it for an instance,
and each person can choose in the list's layout menu (the panel icon next to Filter). The
person's choice wins, then the instance's, then the app's. It is a layout preference, not a
permission. A link with `?open=` opens the panel whatever the setting, and Ctrl or Cmd click on
a title opens the full page in a new browser tab.

- The list narrows: it keeps the title (with the document number under it when the title is
  something else), the State and the first amount, and hides the measures and the bulk bar. When
  the list is narrower than 460 px each record takes two lines. The open row is current
  (`aria-current`) and stays in view.
- Up and Down move along the list and the panel follows; switching rows replaces the history
  entry, so Back closes the panel. Escape and the close button close it and return focus to the
  row. The divider resizes the panel, and the width is kept per list.
- The panel is the record view with `DocFrame`'s panel variant: the same sections in one column.
  The header keeps two buttons and puts the rest in More; key facts sit under the title; the side
  column's Files and Record info close the Details tab and Related is a tab; sections close from
  their heading with the same FoldSection and saved state as the record page (`form.<model>.open`, by
  section title; [collapsible sections](ui.md#collapsible-sections)), and in the panel every section can close; child tables are ChildRows with Open full table; the save
  bar is pinned to the bottom of the panel. Tabs that do not fit scroll sideways.
- Unsaved changes follow the record page: another tab of the record does not ask; another row,
  Close, Escape, Back or leaving the list does (`confirmLeave` with `stays`). Expand opens the
  record page at the same tab with the same record store, so unsaved edits come along without a
  prompt.
- New opens `?open=new`; values in the link fill the new record as on `/app/:model/new`. Create
  shows the new record in the panel and its row in the list. Slips, moves and actions work as on
  the page; a saved or moved record updates its row.
- When the content area is narrower than 900 px the panel covers the list with a back link
  (`Invoices`), and on phones it takes the screen.
- The panel shows only what the model lets it: the schema's `panel` lists hidden `sections`,
  `fields`, `tabs` and `blocks` (`summary`, `files`, `info`), and `order`, the order `Meta.panel`
  names tabs and blocks in (`panelView` in `app/meta.ts`). Details shows while anything in it does.
  A required field that is empty on a new record always shows. When anything is hidden, Details
  ends with "Some parts of this invoice show only on the full page" (the model label) and an Open full page link. The
  full page ignores `panel` ([ui.md](ui.md#what-a-side-panel-shows)).

### What the model decides

| Model | Screen |
|---|---|
| Fields the role can read | The only fields shown anywhere; the rest are absent, not hidden |
| `permissions.create`, `update`, `delete` | New buttons and menu entries, editing in place, bulk Delete and the record's Delete |
| `Meta.list_fields` | List columns (up to eight, without `id`); without it the first eight visible fields |
| `Meta.title_field` | The record title (edited in place) and the pinned first list column |
| `Meta.search_fields` | The list search box and record search in the palette |
| `Meta.layout` | Sections of the Details tab; readable fields it leaves out follow under Other |
| `Meta.open_in` | `"page"` (default) opens a record from the list on its own page; `"panel"` opens it in a side panel beside the narrowed list. The Controls list view and the person's layout menu can change it (see [Side panel](#side-panel)) |
| `Meta.list_tabs`, `Meta.list_tab` | The list's tabs: All and one per value of a State, Select or Link field (the State without `list_tabs`, none with `list_tabs = None`), with counts from `GET /api/{model}/list_tabs`, and the tab the list opens on (`"all"` by default). A Link shows its 8 most used values, labelled by title ([ui.md](ui.md#list-tabs)) |
| `Meta.board` | Names the model's State field, such as `"stage"`, to offer a List and Board switch on its list; the board has one column per state. Without it the list has no board, and `?view=board` opens the table. Any other field fails registration |
| `Meta.autosave` | `True` (default): leaving a record with unsaved edits keeps them for later instead of asking to discard them; `False` keeps the leave prompt ([ui.md](ui.md#unsaved-changes)) |
| `Table(...)` | One tab per table (ChildTable with totals, paging in over 100 rows) and a summary row in Details |
| `compute=`, `initial=`, `applies_when`, `required_when`, `locked_when` | Calculated hints with the rule, initial-value hints, hidden, required and locked fields with the reason |
| `@action` (arguments or `inputs`) | Header buttons; an action with inputs opens a dialog that checks them first. An action that returns `present(...)` opens that slip next. The older record-bound form actions (`dialog: true`) are not shown |
| `Slip` with `source` or `menu` ([slips.md](slips.md)) | A header button on the source model's records, or an entry in New; `?slip=name` on a record opens it. A slip that becomes a record for review opens New filled in, with a note |
| `State` with moves (Phase 4) | List tabs per state unless `Meta.list_tabs` names another field (counts from `GET /api/{model}/list_tabs`), a board when `Meta.board` names the State, with column totals from `GET /api/{model}/aggregate` (with or without declared measures), a header status badge (`show="badge"`, the default) or the stage bar (`show="bar"`) or neither (`show="none"`), move buttons; the badge and the bar offer only the moves this person may take now (`allowed` in `_moves`), the rest are plain text; blocked moves stay visible and say why (`_moves`). With a state tab chosen, the board shows that one column; tabs by another field narrow the cards and keep every column. A board move offers Undo only when a declared move leads back for this role; a move that asks for values is made from the record, whose dialog asks for them |
| Locked states, `editable_when_locked` | Lock banner; only the declared fields stay editable; lines lock |
| Void and revise (Phase 5) | Dialogs with a required reason; banners and links both ways (`revises`, `revised_by`) |
| Unpost and repost | Offered from `_unpost` and `_repost` in the locked banner, the More menu and the unposted banner; Unpost asks for a reason, Repost takes an optional one |
| `Sequence` (Phase 7) | The number in mono as the title; "Number assigned on ..." until then |
| `Meta.measures` (Phase 9) | Model-level measures in a strip over the list (following its filters and search), board column counts and totals of the first declared sum measure (from the aggregate route, with the measure's `where`, as exact amounts); per-record measures in the record's summary strip |
| `Meta.status`, `Select(colors=...)` | A status badge in the record header and the same badge in the list column; never a button unless it is the State with moves to offer |
| `Meta.key_fields` (Phase 10) | The header facts (the first three), and the Key fields side block while a tab other than Details is open (without it: the first fields). Never the summary strip |
| Calculated fields, per-record measures | The summary strip, at most four: calculated values the header facts don't show, then per-record measures. No strip when nothing is left; `Meta.summary = False` turns it off |
| `Meta.relations` (Phase 10) | The Related tab and side block. The side block lists only relations with records, and goes when none has any; the tab lists them all. A relation named after its model counts in that model's words ("Payments, 1 payment"), any other name counts records; an empty relation is one line in the tab, with no table or 0. Rows load when shown, five at a time with a pager, and View all opens the filtered list ([ui.md](ui.md)) |
| `Meta.side_blocks` | Which side blocks show, in order, from `key_fields`, `related`, `files` and `info`. Without it, all four in that order; `()` shows none |
| `panel=False` on a field, `Section` or `Table`; `Meta.panel` | What a record opened in a list's side panel leaves out, or (`Meta.panel`) the only parts it keeps, in order. The schema carries `panel` only when something is hidden; the full page ignores it ([Side panel](#side-panel)) |
| `attachments` | The Files side block, with upload and a remove button on each file (asks first) for roles that can update while the record is not locked |
| `Effects` (Phase 6) | The Postings tab: what was posted, reversals and the balance check |
| Journal (Phase 8) | The Activity tab, first: entries by day with actors named from the page's `users` (the engine as "The system") and a RelativeTime each, field changes old and new, Table rows counted, moves, numbers, postings and their balance, files, messages, calls and follow ups with their due date, assignee and done state; entries marked `partial` say some details are hidden. The note box shows only when the schema's `journal.kinds` lets the role post a note |
| Every record (`_audit`) | Created and Last modified in the Record info side block, as relative times with who ([Relative times](#relative-times)). The record's id is in More > Copy record ID |
| Title, name and State | Shown in the header, so a saved record's Details form leaves them out: the title is edited in place in the header, the State by its badge or bar. A field the header shows but can't edit (a name typed by hand, a State with `show="none"`) stays in the form. A new record's form keeps them all |

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

Appearance, region, start page and sidebar counts are the person's Settings, kept on the server
([settings.md](settings.md)), and so is the theme ([Themes](#themes)). Layout preferences (sidebar, list column
widths, child table columns, quick access, how each list opens records, the side panel's width and
which form sections a person opened or closed) are kept in browser storage under `evlet.<username>.`.
They never hold record data: recent records and recent searches stay in memory for the signed-in
session and go on sign-out. Each recent record keeps when it was last opened; the history button left
of search lists them with the relative time and the exact time on hover. The account area is at the
bottom of the sidebar; the top bar keeps recent records, search and notifications.

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

- Home, accounts, form dialogs, the layout control and Doc
  layout page are not built. Python pages (`evlet.ui` PageView), native panels and custom
  components are not rendered (owner decision); their Python APIs remain.
- Bulk moves and deletes read each record first, one request per record.
- The board loads the first 100 records of the filter. Column counts and totals come from the
  server; the aggregate route has no search, so while searching columns count the loaded cards
  and show no totals.
- Postings show what each target received (rows, reversals, balance); listing the posted rows
  waits for a filter by source on the target's list route.
- Reference fields are read only until the schema lists the models they may point to.
- State metadata (`record.state`) is read when the schema sends it.
- Lookups that a role cannot read stay pending; their saved results keep showing.
