# Evlet UI design handoff

This folder is the complete, approved design for the Evlet user interface. A
session that opens it should be able to do three things:

1. **Rebuild the reference mockup exactly** from `reference/`.
2. **Build the Evlet UI framework and its component library** to this design.
3. **Design and build any new screen** (any model, list, record or transaction)
   so it looks and behaves like the rest.

Read this file top to bottom once. Then keep `reference/mockup.html` open in a
browser while you work. When this document and the mockup disagree, the mockup
wins, and this document should be corrected.

Companion documents in the repo:

- `docs/internal/handoff-model.md`: model engine amendments (expressions, Lines, State,
  void and revise, Effects, Sequence, Journal, Measures). The UI below is
  designed against those concepts.
- `docs/ui.md` and `AGENTS.md`: current UI contract rules. Python stays the
  source of truth; components never appear in model definitions.

---

## 1. What is in `reference/`

| Path | What it is |
|---|---|
| `mockup.html` | The full mockup as one self-contained file. Open it directly in a browser. Inter and JetBrains Mono load from Google Fonts; everything else is inline. |
| `styles.css` | Every style in the mockup, including both themes. The token block at the top is authoritative. |
| `src/1-head.html` | Title, font links and the `<style>` block (same as `styles.css`). |
| `src/2-body.html` | Static shell markup: app grid, scrim, drawer, modal host, popover, bulk bar, floating control, toasts. |
| `src/icons.js` | The icon subset: Lucide static 0.460.0 SVG inner paths keyed by name. |
| `src/3-data.js` | Sample data: users, stages, organizations, contacts, deals, leads, tasks, activity, files, notifications. All fictional. |
| `src/4-core.js` | Helpers, state, routing, shell rendering (sidebar, app bar, top bar), toast, popover, drawer, modal, bulk bar, sparkline. |
| `src/5-views.js` | Home, deals board and list, leads, contacts, organizations, tasks, inbox, reports, timeline and composer. |
| `src/6-actions.js` | Mutations, drag and drop, command palette with quick access, create forms, peek drawers, tooltip, action map, keyboard. |
| `src/7-doc.js` | Doc view engine: record types, fields, sections, blocks, template and user layout, side panel, child table, list table resizing, layout control, Doc layout page with placeholders. |
| `src/9-invoice.js` | Invoice as the transaction sample: states, moves, lock, void, revise, sequence, postings, payments, field-level journal. |
| `src/10-library.js` | The component library page and its catalog data. |
| `src/8-boot.js` | Boot: restores theme, sidebar and view preferences, then renders. |
| `build.sh` | Rebuilds `mockup.html` from `src/` in the correct order. |
| `CHANGELOG.md` | Every approved change, newest first. |

Concatenation order matters: `1-head.html`, `2-body.html`, then one `<script>`
containing `icons.js`, `3-data.js`, `4-core.js`, `5-views.js`, `6-actions.js`,
`7-doc.js`, `9-invoice.js`, `10-library.js`, `8-boot.js`. Later files extend
earlier ones by wrapping functions (for example `docHeader = (dt, d) => ...`).

**The mockup is a design reference, not production code.** It is plain
JavaScript that re-renders whole views with template strings. The real UI is to
be built on Evlet's own fine-grained reactive framework (section 14). Copy
markup, class names, tokens, spacing and behaviour. Do not copy the rendering
approach.

---

## 2. Design principles

- **White and quiet.** White content ground, a slightly grey sidebar, one blue
  accent. No gradients, no heavy shadows, no decorative colour.
- **Colour carries meaning only.** Soft tints mark record types, states and
  icon categories. Semantic colours (green good, amber attention, rose bad) are
  separate from the accent.
- **One layout, many records.** Every record uses the same doc layout. Each
  record type keeps its own identity through its mark, accent, flow, summary
  and one or two unique blocks.
- **Read first, edit in place.** Records open read-friendly. Values become
  inputs on click. Saving one field never needs a Save button.
- **Every action is reversible or confirmed.** Toasts offer Undo. Destructive
  steps that cannot be undone (void) ask for a reason in a dialog.
- **The model decides; the UI explains.** Locked fields, blocked moves,
  calculated values and initial values always say why in place (hint, tooltip,
  banner).
- **Per-user comfort.** Column widths, column order and visibility, density,
  side panel position, hidden blocks, sidebar state, theme, recent searches and
  quick access are saved per user.
- **Plain words.** No jargon on screen. See section 13.

---

## 3. Tokens

Use tokens only. No literal colours in components. Light values sit on `:root`.
Dark values apply under `@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])`, and again under `:root[data-theme="dark"]`,
so an explicit choice wins in both directions.

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#ffffff` | `#0f1115` |
| `--bg-subtle` | `#f8f9fb` | `#13161b` |
| `--bg-muted` | `#f2f4f7` | `#1a1e25` |
| `--side-bg` | `#f4f5f8` | `#14171c` |
| `--surface` | `#ffffff` | `#16191f` |
| `--overlay` | `rgba(16,20,30,.32)` | `rgba(0,0,0,.55)` |
| `--border` | `#e8eaef` | `#252a33` |
| `--border-strong` | `#d8dce3` | `#323844` |
| `--divider` | `#eef0f3` | `#1e222a` |
| `--fg` | `#151821` | `#e8eaf0` |
| `--fg-2` | `#4b5263` | `#b2b8c5` |
| `--fg-3` | `#858b99` | `#7d8494` |
| `--fg-4` | `#a9aeb9` | `#5c6270` |
| `--accent` | `#2f5be8` | `#5b80f5` |
| `--accent-hover` | `#244ed4` | `#6d8ef7` |
| `--accent-fg` | `#ffffff` | `#ffffff` |
| `--accent-soft` | `#eef2fe` | `#1b2440` |
| `--accent-ring` | `rgba(47,91,232,.22)` | `rgba(91,128,245,.3)` |
| `--t-blue` | `#3466e6` | `#7a9cf7` |
| `--t-blue-bg` | `#eef2fd` | `#1a2340` |
| `--t-green` | `#159452` | `#43c27f` |
| `--t-green-bg` | `#e9f7ef` | `#13281d` |
| `--t-amber` | `#c27a0a` | `#e2a53e` |
| `--t-amber-bg` | `#fdf4e3` | `#2a2112` |
| `--t-rose` | `#d6435b` | `#f07b8d` |
| `--t-rose-bg` | `#fdeef0` | `#2e1820` |
| `--t-violet` | `#7553d9` | `#a48df0` |
| `--t-violet-bg` | `#f3effd` | `#221c38` |
| `--t-teal` | `#0e8f8a` | `#3fc2bc` |
| `--t-teal-bg` | `#e6f6f5` | `#10292a` |
| `--t-slate` | `#5f6778` | `#9aa1b0` |
| `--t-slate-bg` | `#f0f2f5` | `#1e222a` |
| `--shadow-sm` | `0 1px 2px rgba(16,24,40,.05)` | `0 1px 2px rgba(0,0,0,.3)` |
| `--shadow-md` | `0 1px 2px rgba(16,24,40,.04),0 4px 12px rgba(16,24,40,.06)` | `0 1px 2px rgba(0,0,0,.3),0 4px 12px rgba(0,0,0,.25)` |
| `--shadow-lg` | `0 2px 6px rgba(16,24,40,.06),0 18px 48px rgba(16,24,40,.16)` | `0 2px 6px rgba(0,0,0,.3),0 18px 48px rgba(0,0,0,.5)` |
| `--font` | `"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif` | `same` |
| `--mono` | `"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace` | `same` |
| `--r-sm` | `6px` | `same` |
| `--r` | `8px` | `same` |
| `--r-lg` | `12px` | `same` |
| `--sidebar-w` | `248px` | `same` |
| `--topbar-h` | `52px` | `same` |

### Tints

Seven tints, each a text colour and a soft background: `blue`, `green`,
`amber`, `rose`, `violet`, `teal`, `slate`. Utility classes: `.c-{tint}` sets
text colour, `.b-{tint}` sets both background and text. Use `.b-*` for icon
tiles, pills, avatars and marks; use `.c-*` for icons inside neutral buttons
and nav items.

| Use | Tint |
|---|---|
| Deal, invoice (accent), inbox | blue |
| Lead, accounting app | teal |
| Contact, HR app, library | violet |
| Organization, tasks, inventory | amber |
| Reports | rose |
| Sales app, paid, won | green |
| Home, neutral, draft, qualification | slate |

State colours: qualification slate, discovery blue, proposal violet,
negotiation amber, won green, lost rose. Invoice: draft slate, issued blue,
paid green, void rose. Lead status: new blue, contacted amber, qualified
green, unqualified slate, converted violet.

### Typography

- Body face **Inter** (400, 450, 500, 550, 600, 650) with features `cv11`,
  `ss01`, `ss03`. Mono face **JetBrains Mono** (400, 500) for document numbers,
  IDs, code and placeholder tokens. Always declare fallbacks.
- Base size 13.5px, line height 1.45. Use `font-variant-numeric: tabular-nums`
  (`.num`) wherever digits align: money, dates, counts, tables.
- Scale in use: 11 (uppercase labels, letter-spacing .04em), 11.5, 12, 12.5,
  13, 13.5 (base), 14 (section titles), 15 (modal titles, stat values), 16
  (library groups), 20 (page titles), 21 (record titles), 22 (greeting, KPI).
  Headings use weight 600 to 650 and letter-spacing -0.01em to -0.02em.
- Dates always display as **DD-MM-YYYY**. Short card dates as `05 Nov`.
  Times as `HH:MM`. Currency as `AED 186,000` (compact `186K`, `1.15M` only in
  tiles and board columns).

### Spacing, radius, elevation

- Radius: `--r-sm` 6px (small controls, nav items), `--r` 8px (buttons,
  inputs, cards, popover items), `--r-lg` 12px (panels, tables, modals,
  grids). Modals and floating panels go to 14 to 16px. Pills 6px, chips 5px,
  switches fully round.
- Controls: buttons and inputs 32px high, small 28px; nav items 32px; table
  rows 44px; grid rows 38px; top bar and workspace row 52px; app bar 48px.
- Gutters: page padding 24px top, 28px sides (16px on phones). Gaps between
  panels 16px, between cards 12px, inside toolbars 8px.
- Shadows: `--shadow-sm` on resting controls, `--shadow-md` on hover and cards
  in focus, `--shadow-lg` on floating things (popover, modal, drawer, toast,
  floating button). Never stack shadows on flat panels.

### Icons

Lucide line icons at stroke 1.75, sizes 12, 14, 16, 18px (`.xs`, `.sm`,
default, `.lg`). Icons take `currentColor`. In the sidebar and buttons, tint the
icon only (`.c-*`), not the background. The full subset used:

`activity`, `arrow-down`, `arrow-left`, `arrow-right`, `arrow-up`, `arrow-up-down`, `arrow-up-right`, `at-sign`, `bell`, `building`, `building-2`, `calendar`, `chart-column`, `check`, `chevron-down`, `chevron-left`, `chevron-right`, `chevrons-up-down`, `circle`, `circle-check`, `circle-dot`, `circle-help`, `circle-x`, `clock`, `columns-3`, `command`, `copy`, `corner-down-left`, `download`, `ellipsis`, `eye`, `eye-off`, `file-text`, `filter`, `flame`, `globe`, `grip-vertical`, `handshake`, `hash`, `house`, `inbox`, `info`, `kanban`, `layers`, `layout-grid`, `link`, `list`, `list-checks`, `lock`, `log-out`, `mail`, `map-pin`, `maximize-2`, `message-circle`, `message-square`, `minimize-2`, `moon`, `package`, `panel-left`, `panel-right`, `paperclip`, `pencil`, `phone`, `pin`, `plus`, `receipt`, `repeat`, `rotate-ccw`, `rows-3`, `search`, `send`, `settings`, `sliders-horizontal`, `sparkles`, `square-kanban`, `star`, `sticky-note`, `sun`, `table-2`, `tag`, `target`, `trash-2`, `trending-down`, `trending-up`, `trophy`, `upload`, `user`, `user-cog`, `user-plus`, `users`, `video`, `wallet`, `x`, `zap`

---

## 4. App shell

```
┌──────────────────────┬─────────────────────────────────────────────┐
│ Workspace switcher   │ Top bar: toggle │ crumbs │ search bell ? New │
├──────────────────────┼─────────────────────────────────────────────┤
│ App bar (tabs)       │                                             │
├──── grey body ───────┤ View (the only scrolling area)              │
│ App name             │                                             │
│ Nav groups           │                                             │
│ …                    │                                             │
│ Doc layout, Library  │                                             │
│ Account, theme       │                                             │
└──────────────────────┴─────────────────────────────────────────────┘
```

- Grid: sidebar 248px, collapses to 60px (icons only, tooltips to the right).
  Under 760px the sidebar becomes a drawer opened from the top bar toggle.
- **Row 1, workspace switcher** (white, 52px, grey line under it): logo tile
  `D`, workspace name, small "Workspace" label, up-down arrows. Opens a menu
  with workspaces and settings.
- **Row 2, app bar** (white, 48px, grey line under it): app tabs (CRM, Sales,
  Accounting, Inventory, HR and Payroll) as 26px tinted tiles in 34px buttons,
  and an All apps grid button on the right. The **active app** is a grey tab,
  41px high, radius 10px on top, with a 1px border on top and sides. It
  overlaps the line by 1px and opens into the grey body below. Its bottom
  corners flare outward with 10px concave curves:

  ```css
  .ab-app.on::before{left:-11px;background:radial-gradient(circle at 0 0,transparent 9.4px,var(--border) 9.6px,var(--border) 10.4px,var(--side-bg) 10.6px)}
  .ab-app.on::after{right:-11px;background:radial-gradient(circle at 100% 0,transparent 9.4px,var(--border) 9.6px,var(--border) 10.4px,var(--side-bg) 10.6px)}
  ```

- **Sidebar body** (`--side-bg`): app name with workspace beside it; Home,
  Inbox (unread badge), My tasks (due count); group "Records" (Leads, Deals,
  Invoices, Contacts, Organizations, Reports, with counts); group "Saved views"
  (coloured dots). Bottom: Doc layout, Library. Footer: account and theme
  toggle. The active nav item is white with a hairline ring.
- **Top bar** (52px): sidebar toggle (`[`), divider, breadcrumbs, then search
  field (240px, `⌘K` hint), notifications bell with a dot, shortcuts help, and a
  primary **New** menu button. On phones the search becomes an icon.
- **Floating layer**: popovers, drawer from the right (440px), centred modals,
  bottom-centre bulk bar, bottom-right floating Layout button, toasts
  bottom-right (bottom-centre when the floating button is present).

---

## 5. Component library

The mockup's **Library** page (sidebar bottom) shows every component live with
its details in a popup. Build components at four levels:

- **Base**: reactive primitives the rest wrap (BaseField, Button, Checkbox,
  Switch, Icon, Divider).
- **Element**: single-purpose pieces (each field type, pills, avatars, chips).
- **Composite**: elements combined (InlineValue, MiniCard, DataTable, KpiTile).
- **Section**: whole screen areas (DocHeader, ChildTable, Journal, Board).

**Fields** are one `BaseField` (label, value cell, required, locked,
applies-when, error, hint) wrapped by one component per model field type. A field
type changes only the input, parsing and display. `InlineValue` shows any field
as text and turns into its input on click.

### Inputs (27)

**BaseField** · Base  
The reactive core every field type wraps. It owns the label, the value cell, required, locked and applies-when rules, the error and the hint. Field types only change the input and how the value is parsed and shown.

- Model: `Any Field`
- Props: `label` (text) From the model field label; `value` (cell) Two-way bound value; `required` (boolean or rule) Static or required_when; `locked` (boolean or rule) Static or locked_when; `applies` (rule) applies_when; hides and clears when false; `error` (cell) Server or client validation message; `hint` (text) Small note beside the label
- Variants: Default, Required with error, Locked, Hidden by rule
- Appears in: Every form, Details tab, Create forms, Action dialogs

**TextField** · Element  
Single line text with a length limit.

- Model: `Text(max_length=…)`
- Props: `…BaseField` All BaseField properties; `model` (field) Text(max_length=…)
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**LongTextField** · Element  
Multi-line text that grows with content.

- Model: `LongText()`
- Props: `…BaseField` All BaseField properties; `model` (field) LongText()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**IntegerField** · Element  
Whole numbers with optional minimum and maximum.

- Model: `Integer(minimum, maximum)`
- Props: `…BaseField` All BaseField properties; `model` (field) Integer(minimum, maximum)
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**DecimalField** · Element  
Exact decimal with a fixed scale. Never a float; values travel as strings.

- Model: `Decimal(scale=…)`
- Props: `…BaseField` All BaseField properties; `model` (field) Decimal(scale=…)
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**CurrencyField** · Element  
Exact money amount with two decimals and the currency shown as a prefix.

- Model: `Currency()`
- Props: `…BaseField` All BaseField properties; `model` (field) Currency()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**PercentField** · Element  
Decimal from 0 to 100 shown with a percent suffix.

- Model: `Percent()`
- Props: `…BaseField` All BaseField properties; `model` (field) Percent()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**DateField** · Element  
Calendar date shown as DD-MM-YYYY, stored as an ISO date.

- Model: `Date()`
- Props: `…BaseField` All BaseField properties; `model` (field) Date()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**DateTimeField** · Element  
Date and time in the user's timezone, stored in UTC.

- Model: `DateTime()`
- Props: `…BaseField` All BaseField properties; `model` (field) DateTime()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**TimeField** · Element  
Time of day without a date or timezone.

- Model: `Time()`
- Props: `…BaseField` All BaseField properties; `model` (field) Time()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**DurationField** · Element  
A length of time typed as 1h 30m, stored in seconds.

- Model: `Duration()`
- Props: `…BaseField` All BaseField properties; `model` (field) Duration()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**BooleanField** · Element  
A switch for true or false. Wraps the Switch element.

- Model: `Boolean()`
- Props: `…BaseField` All BaseField properties; `model` (field) Boolean()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**SelectField** · Element  
One choice from a declared list.

- Model: `Select([...])`
- Props: `…BaseField` All BaseField properties; `model` (field) Select([...])
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**LinkField** · Element  
Picks a record of another model. Shows the record mark and title, and can filter its options by a rule.

- Model: `Link(Model, filter=…)`
- Props: `…BaseField` All BaseField properties; `model` (field) Link(Model, filter=…)
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**EmailField** · Element  
Text checked for an address shape, with a copy action.

- Model: `Email()`
- Props: `…BaseField` All BaseField properties; `model` (field) Email()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**URLField** · Element  
Absolute http or https address.

- Model: `URL()`
- Props: `…BaseField` All BaseField properties; `model` (field) URL()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**PhoneField** · Element  
Phone number, normalized when it starts with a country code.

- Model: `Phone()`
- Props: `…BaseField` All BaseField properties; `model` (field) Phone()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**TagsField** · Element  
A short list of labels. Type and press Enter to add.

- Model: `Tags()`
- Props: `…BaseField` All BaseField properties; `model` (field) Tags()
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**StateField** · Element  
Shows the current state. It never edits directly; the value changes only through moves.

- Model: `State(…, moves=…)`
- Props: `…BaseField` All BaseField properties; `model` (field) State(…, moves=…)
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**SequenceField** · Element  
The document number. Read only, empty until the state that assigns it.

- Model: `Sequence("INV-{year}-{n:05}")`
- Props: `…BaseField` All BaseField properties; `model` (field) Sequence("INV-{year}-{n:05}")
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**ComputedField** · Element  
A read-only value calculated from a rule. Hovering the hint shows the rule.

- Model: `compute=…`
- Props: `…BaseField` All BaseField properties; `model` (field) compute=…
- Built from: BaseField
- Appears in: Details tab, Create forms, Child table cells

**InlineValue** · Composite  
Shows a field as plain text and turns into its field type on click. Enter saves, Esc cancels. Used wherever a record is read more than edited.

- Props: `field` (field) Any field type; `display` (formatter) How the value reads at rest; `onSave` (action) Partial save of one field
- Variants: At rest, Hover with pencil, Editing, Read only
- Built from: BaseField, Icon
- Appears in: Key fields, Details tab, Side panel

**SearchInput** · Element  
Text input with a search icon. Filters as you type and keeps the cursor in place.

- Props: `value` (cell); `placeholder` (text)
- Built from: Icon
- Appears in: List toolbars, Command palette

**Checkbox** · Base  
Square check for selection and task completion.

- Props: `checked` (cell); `label` (text) Optional
- Appears in: Row selection, Task rows, Filter menus

**Switch** · Base  
On and off toggle. BooleanField and the layout control use it.

- Props: `on` (cell)
- Appears in: BooleanField, Layout control, Template

**SegmentedControl** · Element  
Two to four mutually exclusive options shown together.

- Props: `options` (list); `value` (cell); `size` (default or small)
- Appears in: Board and list switch, Density, Report ranges

**QuickAddInput** · Element  
A borderless input at the top of a list that creates an item on Enter.

- Props: `placeholder` (text); `onAdd` (action)
- Appears in: My tasks

### Buttons (10)

**Button** · Base  
The one button. Variants set meaning, sizes set density, an icon is optional.

- Props: `variant` (primary, default, ghost, success, danger, soft danger); `size` (default or small); `icon` (icon) Optional, before the label; `onClick` (action)
- Variants: Primary, Default, Ghost, Success, Danger, Soft danger, Small, Icon only
- Built from: Icon
- Appears in: Everywhere

**IconButton** · Element  
Square button with only an icon. Always carries a tooltip and an accessible label.

- Props: `icon` (icon); `tip` (text) Tooltip and label; `active` (boolean)
- Built from: Icon, Tooltip
- Appears in: Top bar, Side blocks, Drawers

**MenuButton** · Composite  
A button that opens a menu of related actions.

- Props: `label` (text); `items` (list of MenuItem)
- Built from: Button, Popover
- Appears in: New, More actions

**MoveButton** · Element  
Runs a state move. When its guard fails it stays visible, dims and explains why on hover or click.

- Props: `move` (Move) From the State field; `guard` (rule) Move.when; `reason` (text) Shown when blocked
- Variants: Ready, Blocked
- Built from: Button, Tooltip
- Appears in: Doc header

**LinkButton** · Element  
Text link for secondary navigation inside panels.

- Props: `label` (text); `to` (route)
- Appears in: Panel headers, Side blocks

**AddChip** · Element  
Dashed chip that adds an item to a small set.

- Props: `label` (text)
- Appears in: Tags

**FloatingButton** · Element  
Round-ended button fixed to a screen corner. Shows a count when something is changed.

- Props: `label` (text); `count` (text) Optional; `open` (boolean)
- Built from: Button
- Appears in: Layout control

**AppTab** · Element  
App icon in the app bar. The active one becomes a tab that opens into the sidebar.

- Props: `app` (app); `active` (boolean)
- Built from: IconTile, Tooltip
- Appears in: App bar

**NavItem** · Element  
Sidebar link with a tinted icon, label and optional count or badge.

- Props: `icon` (icon); `label` (text); `count` (number); `badge` (number) Unread style; `active` (boolean)
- Built from: Icon, CountBadge
- Appears in: Sidebar

**Tab** · Element  
One tab in a Tabs bar, with an optional count.

- Props: `label` (text); `count` (number); `active` (boolean)
- Appears in: Tabs

### Elements (17)

**Icon** · Base  
Line icon at 12, 14, 16 or 18 pixels. Takes the text colour or one of the tints.

- Props: `name` (text); `size` (xs, sm, md, lg); `tint` (tint) Optional
- Appears in: Everywhere

**IconTile** · Element  
Icon on a soft tinted square. Marks a page, a timeline entry or a card type.

- Props: `icon` (icon); `tint` (tint); `size` (22 to 44)
- Built from: Icon
- Appears in: Page headers, KPI tiles, Quick access

**Avatar** · Element  
Initials on a tinted circle for people. Four sizes.

- Props: `person` (user or contact); `size` (sm, md, lg, xl)
- Appears in: Owners, People blocks, Timeline

**AvatarFilter** · Composite  
Overlapping avatars that filter a list by owner. Selected ones ring, others dim.

- Props: `people` (list); `selected` (cell (set))
- Built from: Avatar
- Appears in: Deals toolbar

**RecordMark** · Element  
First letter of a company on a rounded square. Square for companies, round avatars for people.

- Props: `record` (record); `size` (sm or lg)
- Appears in: Links, Headers, Cards

**StatusPill** · Element  
Dot and label on a tint. One colour per state, taken from the State field.

- Props: `state` (state); `tint` (tint) From the state
- Appears in: Lists, Headers, Key fields

**Chip** · Element  
Small neutral label for tags, counts and flags.

- Props: `label` (text); `icon` (icon) Optional; `tone` (neutral or tint)
- Appears in: Tags, Cards, Tables

**CountBadge** · Element  
Count beside a label. Filled for unread, plain for totals.

- Props: `count` (number); `tone` (filled or plain)
- Appears in: Nav items, Tabs

**Key** · Element  
Keyboard key hint.

- Props: `keys` (text)
- Appears in: Search, Shortcuts, Composer

**Tooltip** · Element  
Dark label with a pointer, shown at once on hover and kept until the pointer leaves. Points down when above, up when below.

- Props: `text` (text); `placement` (auto)
- Appears in: Icon buttons, App bar, Blocked moves, Calculated hints

**ProgressBar** · Element  
Thin bar for a share of a whole, with an optional number.

- Props: `value` (0 to 100); `tint` (tint)
- Appears in: Probability, Board columns, Leaderboard

**ScoreBars** · Element  
Four rising bars and a number. Colour moves from grey to amber to green with the score.

- Props: `score` (0 to 100)
- Appears in: Leads list, Lead header

**ScoreRing** · Element  
Circular gauge with the value in the middle.

- Props: `value` (0 to 100); `label` (text)
- Appears in: Scorecard

**Sparkline** · Element  
Small trend line with an area and a marked last point.

- Props: `values` (list of numbers); `tint` (tint)
- Appears in: KPI tiles

**StageBar** · Element  
One bar split by share, with a colour per segment.

- Props: `segments` (list) value and tint
- Appears in: Pipeline by stage

**PlaceholderToken** · Element  
Shows where data goes in a layout preview.

- Props: `name` (text)
- Appears in: Doc layout placeholders

**Divider** · Base  
Thin vertical or horizontal rule between groups.

- Props: `direction` (vertical or horizontal)
- Appears in: Top bar, App bar, Menus

### Display (23)

**Stat** · Element  
Label, value and a short note. The unit of the summary strip.

- Props: `label` (text); `value` (cell); `note` (text)
- Appears in: Summary strip

**SummaryStrip** · Composite  
Four stats in one bordered row. Values come from the model's measures.

- Props: `stats` (list of up to 4) Field or measure
- Built from: Stat
- Appears in: Doc view

**KpiTile** · Composite  
Headline number with a trend against last period and a sparkline.

- Props: `label` (text); `value` (measure); `delta` (number); `trend` (list)
- Built from: IconTile, Sparkline
- Appears in: Home

**KeyValueList** · Composite  
Two-column label and value list. Values are InlineValues when editable.

- Props: `fields` (list)
- Built from: InlineValue
- Appears in: Key fields, Record info, Peek drawer

**MiniCard** · Composite  
Compact bordered row for a related record: mark, title, subtitle and up to two quick actions.

- Props: `record` (record); `subtitle` (text); `actions` (list) Optional
- Built from: Avatar, RecordMark, IconButton
- Appears in: People, Related, Next step

**TaskRow** · Composite  
Checkbox, title, due and the linked record. Overdue turns the due date red.

- Props: `task` (record); `onToggle` (action)
- Built from: Checkbox, Avatar
- Appears in: My tasks, Home, Tasks tab

**TimelineItem** · Composite  
One journal entry: icon by kind, sentence, time and an optional body.

- Props: `entry` (journal entry); `kind` (note, email, call, changed, moved…)
- Built from: IconTile, MessageCard, ChangeDiff
- Appears in: Journal

**ChangeDiff** · Element  
Field, old value struck through, new value. One line per changed field.

- Props: `changes` (list) label, old, new
- Appears in: Journal

**MessageCard** · Element  
Bordered body for notes, calls and emails. Emails show a subject line.

- Props: `subject` (text) Emails only; `body` (text)
- Appears in: Journal

**EmptyState** · Composite  
Tinted icon, a short heading, what to do next and an optional button.

- Props: `icon` (icon); `title` (text); `text` (text); `action` (button) Optional
- Built from: IconTile, Button
- Appears in: Empty lists, Empty tabs

**StateBanner** · Element  
Full-width note under a header about the record's state, with optional actions.

- Props: `tone` (neutral, info, danger); `text` (text); `actions` (list) Optional
- Built from: Icon, Button
- Appears in: Doc view

**InfoNote** · Element  
Quiet explanation above a block that behaves differently from the rest.

- Props: `text` (text)
- Appears in: Postings, Journal

**FilterChip** · Element  
An active filter with a remove button.

- Props: `label` (text); `onRemove` (action)
- Appears in: List toolbars

**Toast** · Element  
Short confirmation at the bottom of the screen. Undo when the change can be reversed.

- Props: `text` (text); `icon` (icon); `undo` (action) Optional
- Appears in: Every save and move

**DataTable** · Composite  
List table with a sticky header, pinned first columns, resizable columns saved per user, sorting and row selection.

- Props: `columns` (list) From list_fields; `rows` (query); `pinned` (number) Columns to pin; `selectable` (boolean)
- Variants: Plain, Selectable, With totals
- Built from: Checkbox, StatusPill, Avatar
- Appears in: Every list

**BoardCard** · Composite  
Draggable record card for boards: tags, title, linked record, value, date and owner.

- Props: `record` (record); `fields` (list) From key fields
- Built from: Chip, RecordMark, Avatar
- Appears in: Board

**FeedItem** · Composite  
Compact activity line with a link to the record, for feeds across records.

- Props: `entry` (journal entry)
- Built from: IconTile
- Appears in: Home, Inbox

**ListRow** · Composite  
Mark, title and subtitle on the left, value and date on the right.

- Props: `record` (record); `value` (field); `meta` (field)
- Built from: RecordMark
- Appears in: Closing soon

**BarChart** · Composite  
Vertical bars with values on top and labels below. Current period full colour, past ones lighter.

- Props: `series` (measure grouped by period)
- Appears in: Reports

**FileRow** · Composite  
File type tile, name, size, who and when, and a download action.

- Props: `file` (attachment)
- Built from: IconButton
- Appears in: Files tab

**NotificationItem** · Composite  
Icon, sentence and time. Tinted background until read.

- Props: `item` (notification); `unread` (boolean)
- Built from: IconTile
- Appears in: Notifications, Inbox

**Scorecard** · Composite  
Score ring beside three labelled bars.

- Props: `score` (measure); `parts` (list of 3)
- Built from: ScoreRing, ProgressBar
- Appears in: Highlight card

**ChannelRow** · Composite  
Contact channel with a preferred flag and a copy action.

- Props: `channel` (email, phone, WhatsApp); `value` (text); `preferred` (boolean)
- Built from: IconTile, IconButton
- Appears in: Highlight card

### Layout areas (18)

**AppShell** · Section  
The frame of every screen: sidebar with app bar on the left, top bar and one scrolling view on the right. Collapses the sidebar to icons; becomes a drawer on phones.

- Props: `collapsed` (cell) Saved per user; `view` (route)
- Built from: WorkspaceSwitcher, AppBar, SidebarNav, TopBar
- Appears in: Everything

**WorkspaceSwitcher** · Composite  
Logo, workspace name and switch arrows. First row of the sidebar.

- Props: `workspace` (workspace)
- Built from: Icon
- Appears in: Sidebar

**AppBar** · Composite  
Row of AppTabs on white. The active app opens into the grey sidebar below with soft corners.

- Props: `apps` (list); `active` (app)
- Built from: AppTab
- Appears in: Sidebar

**SidebarNav** · Composite  
Grouped NavItems with small uppercase group labels. Scrolls on its own.

- Props: `groups` (list); `active` (route)
- Built from: NavItem
- Appears in: Sidebar

**TopBar** · Composite  
Sidebar toggle, breadcrumbs, search, notifications and the New menu.

- Props: `crumbs` (list)
- Built from: IconButton, Breadcrumbs, SearchInput, MenuButton
- Appears in: App shell

**Breadcrumbs** · Element  
Where you are, with links back up.

- Props: `items` (list)
- Appears in: Top bar

**PageHeader** · Composite  
Page icon, title, optional count and the page actions on the right.

- Props: `icon` (icon); `title` (text); `actions` (list)
- Built from: IconTile, Button
- Appears in: List pages

**Tabs** · Composite  
Row of Tabs with an underline in the record accent. Scrolls sideways on phones.

- Props: `tabs` (list); `active` (cell)
- Built from: Tab
- Appears in: Doc view, Lists

**Toolbar** · Composite  
Search, filters and a summary in one wrapping row above a list.

- Props: `left` (slots); `right` (slots)
- Built from: SearchInput, AvatarFilter, FilterChip
- Appears in: List pages

**Panel** · Composite  
Bordered card with a header row and a body. The standard container on dashboards.

- Props: `title` (text); `action` (link) Optional; `body` (slot)
- Built from: LinkButton
- Appears in: Home, Reports, Tasks

**DocFrame** · Section  
The record layout: main column on the left, side panel on the right (or left). Side panel drops below on narrow screens.

- Props: `side` (left, right or off) Per user; `density` (comfortable or compact) Per user
- Appears in: Every record

**SideBlock** · Composite  
Titled block inside the side panel, with an optional header action.

- Props: `title` (text); `action` (button) Optional; `body` (slot)
- Appears in: Side panel

**Drawer** · Composite  
Panel that slides in from the right over the view. For quick looks without leaving the page.

- Props: `title` (text); `body` (slot); `footer` (slot) Optional
- Appears in: Record peek

**Modal** · Composite  
Centred dialog with a header, body and footer. Esc and the scrim close it.

- Props: `title` (text); `body` (slot); `footer` (slot)
- Appears in: Create forms, Action dialogs, Shortcuts

**Popover** · Composite  
Floating menu anchored to its button. Repositions near screen edges.

- Props: `anchor` (element); `items` (list of MenuItem)
- Appears in: Menus, Filters, Columns

**BulkBar** · Composite  
Dark bar that rises from the bottom when rows are selected.

- Props: `count` (number); `actions` (list)
- Appears in: Lists

**FloatingPanel** · Composite  
Panel that opens above a FloatingButton and closes on outside click or Esc.

- Props: `title` (text); `body` (slot); `footer` (slot)
- Built from: FloatingButton
- Appears in: Layout control

**StickyBar** · Composite  
Bar that sticks to the top of the view while the page scrolls.

- Props: `left` (slot); `right` (slot)
- Appears in: Doc layout

### Sections (18)

**DocHeader** · Section  
Record mark, record type, title, key facts and the main actions. Moves and actions come from the model.

- Props: `record` (record); `meta` (key fields); `actions` (moves and actions)
- Built from: RecordMark, StatusPill, MoveButton, Button, MenuButton
- Appears in: Every record

**StatusFlow** · Section  
The states of the State field as connected steps. Done steps tint, the current one fills, terminal states turn green or red.

- Props: `state` (State field); `moves` (list) Click runs a move
- Variants: In progress, Won, Lost or void
- Appears in: Doc view

**LockBanner** · Section  
The record's lifecycle under the header: draft, locked with void and revise, or void with the reason.

- Props: `record` (record)
- Built from: StateBanner, Button
- Appears in: Transaction records

**ActivityComposer** · Section  
One box for a note, email, call log or task. Cmd Enter saves to the journal.

- Props: `modes` (list) note, email, call, task; `record` (record)
- Built from: SegmentedControl, Button, IconButton, Key
- Appears in: Activity tab

**Journal** · Section  
Append-only history grouped by day: notes, messages, field changes, moves, postings and payments.

- Props: `record` (record); `kinds` (filter) Optional
- Built from: TimelineItem, ChangeDiff, MessageCard
- Appears in: Activity tab

**ChildTable** · Section  
Lines of a record. Pinned row and first column, resizable and reorderable columns saved per user, Tab and Enter editing, inline row expand, focus view and live totals. Locks with the record.

- Props: `lines` (Lines field); `columns` (line fields); `totals` (list); `locked` (rule)
- Built from: Checkbox, Button, DataTable, BaseField
- Appears in: Items, Lines, Branches

**FormSection** · Section  
Titled group of fields in two columns. Long text spans both.

- Props: `title` (text); `note` (text); `fields` (list)
- Built from: InlineValue, BaseField
- Appears in: Details tab

**PostingsTable** · Section  
Rows the record wrote to other models, with reversals and a balance check. Read only.

- Props: `effects` (Effects)
- Built from: InfoNote, DataTable, Chip
- Appears in: Transaction records

**Board** · Section  
Columns by state with counts, totals and drag to move.

- Props: `group_by` (State field); `measure` (measure) Column total; `card` (BoardCard)
- Built from: BoardCard, StatusPill, ProgressBar
- Appears in: Deals

**ListView** · Section  
Page header, saved views, toolbar and DataTable, with a board switch when the model has a State.

- Props: `model` (model); `views` (saved views)
- Built from: PageHeader, Tabs, Toolbar, DataTable
- Appears in: Every list

**CommandPalette** · Section  
Search across records, pages and actions, with recent searches and a customizable quick access grid.

- Props: `recent` (list) Last 4; `quick` (list) 1 or 2 rows
- Built from: SearchInput, QuickAccess, Key
- Appears in: Cmd K

**QuickAccess** · Section  
Grid of tiles for modules, reports and documents. Documents carry a plus or list badge.

- Props: `tiles` (list); `rows` (1 or 2)
- Built from: IconTile
- Appears in: Command palette

**LayoutControl** · Section  
Floating control on records to show or hide blocks, set density and side panel position. On the layout page it edits the template.

- Props: `scope` (user or template)
- Built from: FloatingButton, FloatingPanel, Switch, SegmentedControl
- Appears in: Every record, Doc layout

**CreateForm** · Section  
Dialog with the fields a new record needs, validated before it closes.

- Props: `model` (model); `fields` (required fields)
- Built from: Modal, BaseField, Button
- Appears in: New menu

**ActionDialog** · Section  
Small dialog that collects an action's inputs before it runs.

- Props: `action` (model action); `inputs` (fields)
- Built from: Modal, BaseField
- Appears in: Record payment, Void, Revise

**RecordPeek** · Section  
Drawer with a record's header, quick actions and details, plus a button to open the full record.

- Props: `record` (record)
- Built from: Drawer, KeyValueList, MiniCard
- Appears in: People, Related

**NotificationsPanel** · Section  
Popover list of recent notifications with mark all read and a link to the inbox.

- Props: `items` (list)
- Built from: Popover, NotificationItem
- Appears in: Top bar

**SidePanel** · Section  
Stack of side blocks: next step, key fields, highlight card, people, related records, tags and record info. Each can be turned off.

- Props: `blocks` (list) From the template and user layout
- Built from: SideBlock, KeyValueList, MiniCard, Scorecard
- Appears in: Every record


---

## 6. Screens

### Home

Greeting with date and today's counts; quick actions (Log a call, New lead,
New deal). Four KPI tiles with delta and sparkline. Two columns: Pipeline by
stage (stage bar and rows) and Closing soon (list rows); My tasks and Recent
activity (feed items linking to records).

### List pages (deals, leads, contacts, organizations, invoices)

Page header with icon tile and actions. Tabs act as saved views with counts.
Toolbar: search, avatar owner filter, filter chips, Clear, summary on the
right. DataTable rules:

- Sticky header. The first content column (and the selection column if
  present) is pinned while scrolling sideways, with a hairline on its right edge.
- Every header has a resize handle on its right edge (visible on hover, accent
  while dragging). Double-click resets. Widths are saved per user per list.
- Sort by clicking a header (chevron shows direction). Row click opens the
  record. Selection checkboxes raise the bulk bar (Assign, Move stage, Delete
  with Undo, Clear).
- Deals also have a Board: one column per state with tinted pill, count,
  compact total and a progress bar of value share; cards drag between columns;
  Add deal at the foot of each column; Undo toast after a move.

### Records (doc view)

See section 7.

### Command palette (`⌘K`)

720px wide, 64px input row. Empty query shows **Recent** (last four searches,
remove each, Clear) and **Go to**: a quick access grid of tiles (6 columns, 4 on
phones) in 1 or 2 rows chosen by the user. Customize turns on edit mode: tiles
show remove buttons, empty slots appear dashed, and a picker lists Modules,
Reports and Documents as toggle chips (documents carry a plus badge for "new"
or a list badge for "list"). Typing switches to results: Records, Pages and
actions, Preferences. Arrow keys move through rows and the grid (left and right
inside the grid), Enter opens.

### Doc layout page

Sticky bar with title and a switch: **Sample data** shows a real record in the
shared layout; **Placeholders** shows the same layout with tokens (`{title}`,
`{record type}`, `{meta}`, `{status flow}`, `{metric}`, `{child table}`,
`{column 1}`, `{label}`, `{value}`, `{actor}`, `{action}`, `{time}`,
`{highlight card}` and so on). Tabs work in both modes. The floating control on
this page is **Layout template** and edits defaults for every record.

### Library page

Sidebar nav is replaced by the library index: back arrow, filter, groups with
counts, items with a level dot. Content: sticky header with level legend, group
sections, card grid (wide cards for sections). Scrolling highlights the item in
view and keeps it visible in the index; clicking an index item scrolls to the
card below the sticky header. Card click opens details in a popup only.

---

## 7. Doc view

### Anatomy

```
┌─────────────────────────────────────────────┬──────────────────┐
│ Header: mark │ TYPE · Title · meta │ actions │ Side panel       │
│ State banner (transactions)                 │  Next step       │
│ Status flow                                 │  Key fields      │
│ Summary strip (4 stats)                     │  Highlight card  │
│ Tabs: Activity Details {Child} Emails …     │  People          │
│ Tab body                                    │  Related         │
│                                             │  Tags            │
│                                             │  Record info     │
└─────────────────────────────────────────────┴──────────────────┘
```

Main column padding 22px 28px; side panel 340px on `--bg-subtle` with a left
hairline. Under 1100px the side panel moves below. The record's accent
(`--acc`, `--acc-bg`) comes from its type tint and colours the eyebrow, the
active tab underline, the current step and the score ring.

### Blocks

The template decides which blocks show for everyone; each user can hide or show
blocks for themselves per record type from the floating **Layout** button.
Blocks that differ from the template are marked with an amber dot. Header is
always on.

| Key | Block | Group | Applies to |
|---|---|---|---|
| `header` | Header (always on) | Main | all |
| `flow` | Status flow | Main | deal, lead, org, invoice |
| `summary` | Summary strip | Main | all |
| `composer` | Activity composer | Main | all |
| `t_details` | Details tab | Tabs | all |
| `child` | Child table tab | Tabs | deal, org, invoice |
| `effects` | Effects tab | Tabs | invoice |
| `t_emails` | Emails tab | Tabs | all |
| `t_tasks` | Tasks tab | Tabs | all |
| `t_notes` | Notes tab | Tabs | all |
| `t_files` | Files tab | Tabs | all |
| `side` | Side panel | Side panel | all |
| `next` | Next step | Side panel | all |
| `key` | Key fields | Side panel | all |
| `highlight` | Highlight card | Side panel | lead, contact |
| `people` | People | Side panel | all |
| `related` | Related records | Side panel | all |
| `tags` | Tags | Side panel | all |
| `info` | Record info | Side panel | all |

### Identity per record type

| Record type | Label | List route | Icon | Accent tint |
|---|---|---|---|---|
| `deal` | Deal | `deals` | `handshake` | blue |
| `lead` | Lead | `leads` | `target` | teal |
| `contact` | Contact | `contacts` | `users` | violet |
| `org` | Organization | `orgs` | `building-2` | amber |
| `invoice` | Invoice | `invoices` | `receipt` | blue |

Person records use round avatars; company and document records use rounded
square marks. Unique blocks: deals have Items (child table); organizations have
Branches (child table); leads have a Scorecard highlight; contacts have a
Channels highlight; invoices have Lines and Postings.

### Header

Eyebrow (record type, uppercase, accent), title (editable in place when the
model allows; never for sequence numbers), meta row (money in bold, linked
record, dates with overdue in rose, owner avatar), actions on the right: plain
buttons for contact actions, the primary move or action in blue (or green for
"won"), and a More menu (copy link, layout, void, revise, delete when allowed).

### Status flow

Chevron steps the full width. Done steps use `--acc-bg` and a check, the current
step is filled `--acc`, terminal good is green, terminal bad is rose. Clicking
a step requests that move; if it is not allowed the step does nothing but toast
the reason.

### Summary strip

Four stats in one bordered row: label, value, small note. Values come from
model measures or key fields (deal: amount, weighted, days in stage, closes;
invoice: total, outstanding, due, payments).

### Tabs

Activity is always first. Then Details, the child tables, Postings (effects),
Emails, Tasks, Notes, Files. Counts show in a small grey badge.

- **Activity**: composer (Note, Email, Log call, Task) then the journal grouped
  by day: Today, Yesterday, then weekday and date. Entry kinds: note, email,
  call, meeting, task, file, created, changed (with old and new values), moved,
  posted or reversed effects, voided (with reason), revised (with link),
  payment. Transaction records show "Append-only history" above.
- **Details**: form sections from the model layout, two columns, long text
  full width. Each value is an InlineValue. Hints beside labels: `ƒ calculated`
  (violet, tooltip shows the rule), `from customer` (teal, for initial values
  not yet edited), `locked` (lock icon), `editable after issue`. Child tables
  appear as a summary row linking to their tab.

### Side panel

Titled blocks: Next step (task card or a dashed prompt), Key fields (with All
fields link), highlight card, People (mini cards with copy email and phone),
Related (linked records, payments, revision links), Tags (chips and add), Record
info (owner, created, last change, ID, journal count and chain status).

### Transactions (invoice pattern)

- **Banner under the header**: Draft (grey, explains what issuing does), Issued
  or Paid and locked (blue, Revise and Void buttons when issued), Void (rose,
  reason, replacement link).
- **Moves**: shown as header buttons. A blocked move stays visible, dims, and
  explains why on hover or click (tooltip and toast). Primary action after issue
  is Record payment.
- **Number**: shown in mono as the title once assigned; "Draft invoice" and
  "Assigned on issue" before.
- **Locking**: fields show a lock hint and stop editing; the child table hides
  add, duplicate and delete and shows "Locked. Lines can't change after the
  record is issued."
- **Void** dialog requires a reason and says the number stays used. **Revise**
  requires a note, voids, and opens an editable copy on its Lines tab.
- **Postings** tab: info note, table of posted rows (date and time, account,
  party, source chip, debit, credit), reversals with a rose repeat icon, footer
  with totals and Balanced or Not balanced.
- **Action dialogs** (Record payment): small modal with the action's inputs,
  validated in place (for example "Outstanding is only AED 25,200").

### Child table

- Toolbar: icon tile, title, row count, selection info with Duplicate and
  Delete, Columns menu, Focus button.
- Pinned control column (checkbox, row number, expand chevron) and pinned first
  data column with a shadow once scrolled sideways. Sticky header and sticky
  totals row. Calculated columns carry a lock icon in the header and are not
  editable.
- Click a cell to edit. Tab moves to the next editable cell (wrapping rows),
  Shift Tab back, Enter to the cell below, Esc cancels. Selects commit on change.
- Resize from header edges, double-click resets; Columns menu shows and hides
  columns (eye), reorders with up and down, keeps the first column pinned, and
  resets. All saved per user per table.
- **Row expand** (instead of a popup): the row opens in place underneath with
  every field (hidden columns included), previous and next, Duplicate, Delete,
  Done. The panel stays aligned to the visible width while the table scrolls.
- **Focus view**: the table takes over the whole content area below the top bar,
  keeps the record name as a crumb, and Esc returns. The floating button hides
  while focused.
- Under the table: Add row, keyboard hints, and totals (net, VAT, grand total).

### Layout control

Floating pill bottom-right ("Layout", with "N hidden" in amber). Opens a 304px
panel: density (Comfortable, Compact), side panel (Left, Right), switches
grouped Main, Tabs, Side panel (side blocks disable when the side panel is off),
Reset to template, link to the template page. Closes on outside click and Esc.
Only the opening animates.

---

## 8. Interaction rules

- **Tooltips** appear immediately on hover and stay until the pointer leaves.
  Dark label with a 8px rotated-square pointer: above the target pointing down,
  below pointing up when there is no room above, right of collapsed-sidebar
  items pointing left. Never on touch devices. Hide on scroll and when the
  target disappears.
- **Toasts**: dark, bottom-right, 2.8s (5s with Undo). Messages say what
  happened ("Moved to Discovery", "Issued as INV-2026-00042. Posted 3 ledger
  rows.").
- **Popovers** anchor to their button, flip above near the bottom edge, close
  on outside click, Esc or route change. Only one open at a time.
- **Drawers** slide from the right for quick looks and always offer "Open
  record".
- **Modals** are for creating records and running actions with inputs. They
  validate in place, focus the first field, save on Cmd Enter and close on Esc
  or the scrim.
- **No browser dialogs** (`alert`, `confirm`, `prompt`). Confirmations are
  built into the page.
- **Copy actions** (email, phone, link) copy and confirm with a toast showing
  what was copied.

### Keyboard

| Keys | Action |
|---|---|
| `⌘K` / `Ctrl K` | Search and commands |
| `C` | Create in the current context |
| `/` | Focus the page search |
| `G` then `H` `D` `L` `T` `C` `O` `R` `I` | Go to home, deals, leads, tasks, contacts, organizations, reports, inbox |
| `[` | Toggle the sidebar |
| `?` | Shortcuts |
| `⌘ Enter` | Save the composer or a form |
| `Esc` | Close the top layer: popover, modal, drawer, layout panel, focus view, then selection |

---

## 9. Responsive and accessibility

- Breakpoints: 1100px (dashboard grids to one column, doc side panel below),
  760px (sidebar becomes a drawer, phone gutters 16px, summary strip 2 by 2,
  form one column, palette 4-column grid, labels hide on secondary buttons).
- Never scroll the page sideways. Only tables, grids and boards scroll
  sideways inside their own containers.
- Respect safe-area insets on fixed elements. Respect reduced motion (no
  transitions or animations).
- Every icon-only button has a tooltip and an accessible label. Focus rings use
  a 2px accent outline. Dialogs and panels are reachable by keyboard and close
  on Esc. Switches use `role="switch"` with `aria-checked`.

---

## 10. Per-user preferences

The mockup keeps these in browser storage under the `crmws.` prefix. In Evlet
they belong in server-side user settings so they follow the user.

| Key | Holds |
|---|---|
| `theme` | light or dark (unset follows the system) |
| `collapsed` | sidebar collapsed |
| `dealView` | board or list |
| `recentQ` | last four searches |
| `qaIds`, `qaRows` | quick access tiles and row count |
| `layoutMode` | Doc layout page: sample or placeholders |
| `docTpl2` | layout template (shared default; admin scope in Evlet) |
| `docPrefs.<user>` | per record type: hidden blocks, density, side panel position |
| `grid.<user>.<table>` | child table column widths, hidden columns, order |
| `list.<user>.<list>` | list column widths |

---

## 11. Model to UI mapping

| Model concept (`docs/internal/handoff-model.md`) | UI |
|---|---|
| Field types | One field component per type over BaseField |
| `compute=` | ComputedField or `ƒ calculated` hint; read only; tooltip shows the rule |
| `initial=` | Value fills when its source changes; hint until the user edits |
| `applies_when` / `required_when` / `locked_when` | Hide and clear, required star and error, lock hint |
| `Lines` | ChildTable tab; summary row in Details |
| `State` with moves | StatusFlow, move buttons, blocked reasons |
| Locked states | LockBanner, locked fields, read-only lines |
| Void, revise | Dialogs with required reason; banners and links both ways |
| `Effects` | Postings tab with reversals and balance check |
| `Sequence` | Mono number title; "Assigned on issue" |
| `Journal` | Activity tab entries including field diffs |
| `Measures` | Summary strip, KPI tiles, board column totals |
| `key_fields`, `relations` | Key fields block, Related block |
| `@action` with inputs | Primary button plus ActionDialog |
| `Meta.layout` sections | FormSection groups in Details |

---

## 12. Building new screens

1. Start from the model. List its fields, Lines, State, actions, measures and
   relations.
2. Use the doc layout. Do not invent a new record layout. Choose: accent tint,
   mark (person or company), summary stats (four), unique highlight block if
   any, child tables, side blocks.
3. Lists: pick list fields (up to eight), the pinned column, saved view tabs,
   and a board only if the model has a State.
4. Reuse components from the catalog. If something new is needed, build it at
   the lowest level that works and add it to the Library with its details.
5. Check: both themes, 390px wide, keyboard only, empty states, locked and
   blocked states, long values, 1,000-row child table.

---

## 13. Copy

- Plain English, short sentences, no jargon, no em dashes.
- Sentence case for everything except proper nouns.
- Buttons say what happens: "Issue", "Record payment", "Void invoice", "Create
  deal". Toasts confirm in past tense.
- Errors say what is wrong and how to fix it: "Outstanding is only AED 25,200",
  "Choose a customer first", "Deal name is required".
- Empty states: what goes here and how to add the first one.
- Explain rules where they bite: "Locked. Only remarks can change. To correct
  it, void or revise."
- Money in AED by default, dates DD-MM-YYYY, timezone GST.

---

## 14. Building the Evlet UI framework

Target: no virtual DOM, fine-grained reactivity, Evlet's own mechanism, tied to
the model's reactive rules.

1. **Reactive core**: `cell`, `derive`, `effect`, `batch`, ownership scopes for
   cleanup, named cells (`record.lines[3].net`) for debugging.
2. **Model runtime**: a record store per record (a cell per field, keyed row
   stores for Lines), `compute` as derives using a JS evaluator of the same
   expression ops as Python, rules as boolean derives bound to inputs,
   `initial` as effects guarded by a touched flag, incremental `Sum` over lines,
   a draft layer over server values, server results replace the draft on save.
3. **Exact money**: fixed-point decimals on `BigInt` with the same scale and
   rounding as the Python fields.
4. **Rendering**: Python view schemas build DOM once and bind each dynamic
   property to one effect; framework components use runtime-parsed templates
   cloned per use. Control flow helpers: `when`, `match`, keyed `each`, `portal`.
5. **Conformance**: one shared set of expression fixtures run against both the
   Python and JS evaluators in CI.

Build order for components: Icon, Button, Tooltip, BaseField and the field
types, InlineValue, Popover, Modal, Drawer, Toast, DataTable, ChildTable, then
the doc view sections, then lists and the palette. Port each against the mockup
screen it appears in.

---

## 15. Rebuilding the mockup itself

```sh
cd docs/ui-design/reference
sh build.sh            # writes mockup.html from src/
open mockup.html       # or any browser
```

The mockup needs no server. Sample data resets on reload; preferences persist in
browser storage. To change it, edit the relevant `src/` file, rebuild, check
light and dark at desktop and 390px, and add a dated entry to `CHANGELOG.md`.

---

## 16. Approved history

Working artifact: CRM Workspace v2 (updated in place from here on)
Locked baseline: CRM Workspace Concept (v1, approved 01-11-2026, never edited)

### 01-11-2026 (component library)

#### Added
- Library (bottom of the sidebar): 113 components drawn from the mockup, each a live demo rendered by the same code the screens use.
- Six groups: Inputs (27), Buttons (10), Elements (17), Display (25), Layout areas (18), Sections (16).
- Four levels marked on every card: Base, Element, Composite, Section. Fields are one BaseField wrapped by 20 field types, each mapped to its model field.
- Library index replaces the sidebar nav while in Library: back arrow, filter, grouped list with level dots. It follows the scroll position and keeps the active item in view; clicking an item scrolls to it.
- Details only in a popup: description, model mapping, properties, variants, what it is built from, which components use it (both clickable), and where it appears.
- Demos are safe: actions that would change real records open the details popup instead. Inputs, switches, segmented controls, the child table, menus, drawer, dialog, toast and search stay live.

### 01-11-2026 (transaction document: Invoice)

#### Added
- Invoices list (sidebar, under Records) with Draft, Issued, Paid and Void views, outstanding and overdue totals. Pinned first column and resizable columns like other lists.
- Invoice records on the shared doc layout, built to match the model handoff:
  - States with moves: Draft, Issued, Paid. Moves check their guards and explain why when blocked (Issue needs a customer and valid lines; Mark paid needs zero outstanding).
  - Number assigned on issue (INV-2026-00042 and so on), never reused.
  - Draft banner, locked banner after issue (only remarks stay editable), void banner with reason and replacement link.
  - Void asks for a reason and writes mirror reversal rows. Revise voids and opens an editable draft copy linked both ways.
  - Calculated fields marked with a calculated hint that shows the expression on hover. Credit days fill from the customer until edited.
  - Lines tab uses the child table and locks after issue.
  - Postings tab shows the ledger rows the invoice wrote, with reversals and a balance check.
  - Record payment action with inputs (amount, date, method), validated against outstanding.
  - Activity now records field changes with old and new values, moves, postings, voids, revisions and payments. Marked append-only.
- Create invoice from a deal (deal More menu) copies the deal's items into a draft.
- Search palette: Invoice list and New invoice in quick access. C creates an invoice on invoice screens.
- Effects tab added to the Layout control as a layout block.

### 01-11-2026 (layout page and sidebar fix)

#### Changed
- Sidebar top split into two rows: workspace switcher on the first row, app icons on the second. A grey line separates them. The active app tab still opens into the sidebar. An All apps button sits at the end of the app row.
- Tooltips appear instantly on hover and stay until the pointer leaves. They carry a pointer: above an item it points down, below it points up, beside the collapsed sidebar it points left. No tooltips on touch devices.
- Doc view template page removed. Replaced by Doc layout (bottom of sidebar): a live sample record with a switch at the top between Sample data and Placeholders ({title}, {status flow}, {child table} and so on).
- The floating control on Doc layout edits the template for every record. On records it still edits only your own view, and links to Doc layout.
- Block names are now layout terms only: Child table tab (was Items / Branches), Highlight card (was Scorecard / Channels). No record-type labels in the controls.

### 01-11-2026 (doc view update)

#### Added
- Doc view template: one layout for Deal, Lead, Contact and Organization. Same header, flow, summary, tabs and side panel positions everywhere.
- Each record type keeps its own identity: accent colour, header (company mark or person avatar), flow (deal stages, lead status, org relationship), summary numbers and unique blocks (Items for deals, Branches for orgs, Scorecard for leads, Channels for contacts).
- Details tab: full form in sections, every field editable in place.
- Leads, contacts and organizations now open as full records. The side drawer stays for quick looks from inside a record, with an Open record button.
- Doc view template page, at the bottom of the sidebar: anatomy preview per record type plus a blocks by record type matrix. Changes here apply to everyone.
- Floating Layout control on every record: show or hide blocks, density (comfortable or compact), side panel left or right, reset to template. Saved per user, per record type. Blocks that differ from the template are marked.
- Child table (deal Items, org Branches): pinned row and first column, sticky header and totals, horizontal scroll, drag to resize columns (double-click resets), show, hide and reorder columns, all saved per user. Tab and Enter move between cells. Bulk select, duplicate, delete with undo. Deal amount follows the items net total, VAT 5% per line.
- Row expand: opens the full row inline under itself with every field (hidden columns included), previous and next, duplicate and delete. Focus view: the table takes over the whole content area, Esc to return.
- List views (deals, leads, contacts, orgs): first column pinned while scrolling sideways, columns resizable, widths saved per user.

### Changed
- App bar moved to the top of the sidebar on a white strip: workspace switcher, divider line, app icons. The active app is a grey tab that opens into the sidebar below.
- Search moved to the top bar next to the bell. Sidebar toggle moved to the top bar.
- Tooltips show only on devices with hover.

### 01-11-2026

#### Changed
- Apps moved out of the sidebar list into a small icon row at the bottom of the sidebar, with hover labels and an All apps grid.
- Search window rebuilt: wider, taller input, Recent section with the last 4 searches, Go to quick access grid.
- Quick access can be customized: 1 or 2 rows, add or remove tiles from Modules, Reports and Documents. Document tiles open a list or a new form.
- Keyboard navigation in search now covers the tile grid (arrow keys).

### Fixed
- Collapsing the sidebar hid the expand button. It now stays visible.
- Search window spilled past the screen edge on phones.
