# Opening a record in a side panel from a list — plan

Status: approved by the owner on 03-11-2026 with every proposed decision, and implemented. The
clickable mockup is published as an Artifact (link in the PR). The user guide is "Side panel"
in [ui-framework.md](../ui-framework.md).

What the implementation does differently from this plan, for the owner to review:

- Tabs that do not fit in the panel scroll sideways; there is no "More" tab menu yet.
- Up and Down move between rows; J and K are not bound, and moving past the last row does not
  turn the page.
- The switch prompt keeps today's Cancel and Discard (decision 4). The mockup's "Save and
  continue" was not added.
- `?open=` carries the 13-character text id (PR 92). An older decimal id in `?open=` opens the
  same record, as the record page does.

## What exists today

- A list (`app/list-page.ts`) opens a record with `router.go('record', ...)`: the record
  replaces the list at `/app/:model/:id`. A board (`?view=board`) does the same.
- Controls > Models has three tabs that shape these screens for an instance: Fields,
  Form layout (the sections of the Details tab) and List view (columns, page size, sort).
  The record page has one layout: a header, banners, status flow, summary strip, tabs
  (Activity, Details, one per Table, Related, Postings), a right-hand side column (Key
  fields, Related, Files, Record info) and the save bar. `DocFrame` already supports the
  side column on the right, the left or off.
- PR 77: switching tabs inside a record does not ask; leaving a record with unsaved
  changes asks (`router.confirmLeave` plus `confirmDiscard`).
- PR 92: records are addressed by a 13 character text id (`01hc1sd9vc1ye`). Next come the
  standard `title` (the display heading) and `name` (the document number) columns.

## 1. The option

A second way to open records: **Side panel**. "Full page" stays the default and keeps
working exactly as now.

| Level | Where | Who | Example |
|---|---|---|---|
| App default | `Meta.open_in = "page"` or `"panel"` on the model | App developer, Python | `class Invoice(Model): class Meta: open_in = "panel"` |
| Instance | Controls > Models > List view: "Open records in" | Administrator | Changes the default for this instance, like columns and page size today |
| Person | The list's view menu: "Open in: Full page / Side panel" | Anyone who can read the list | Remembered per model in browser storage (`evlet.<user>.openIn.<model>`) |

The person's choice wins over the instance, and the instance wins over the Python
default. This is a layout preference like column widths, not a permission, so it never
widens what anyone can see. Python stays authoritative: `open_in` is validated in
`Meta` (only `page` or `panel`), sent in `/api/schema`, pinned in the public API snapshot,
and exported by Controls export as `ListView(open_in=...)`.

Ctrl or Cmd click and middle click on a row always open the full page in a new browser
tab, whatever the setting. The board view uses the same setting.

## 2. List behaviour

- **Narrowing.** The list keeps its toolbar, search, filters and state tabs. The measures
  strip and bulk checkboxes hide while the panel is open. Columns drop from the right
  until the list fits: the title (with `name` under it in mono), the State chip and the
  first money or key field stay. Below 460 px of list width each row becomes two lines:
  title and amount, then `name` and status.
- **Selected row.** Highlighted with the accent edge and `aria-selected`, scrolled into
  view whenever the panel record changes (also after paging or a reload).
- **Keyboard.** Up and Down (or J and K) move between rows and the panel follows. Enter
  opens the focused row. When the panel record has unsaved changes, moving asks first.
  Moving past the last row of a page goes to the next page.
- **Divider.** A drag handle between list and panel. Width is clamped (list at least
  320 px, panel at least 380 px and at most 70 percent) and remembered per model
  (`evlet.<user>.panelWidth.<model>`). Double click resets it. It is a focusable
  `role="separator"` that Left and Right arrows resize.
- **Expand.** Opens the full record page at the same tab. Unsaved edits travel with it
  (the record store is handed over, not reloaded), so expanding never asks.
- **Close.** The close button or Escape. Asks only when there are unsaved changes. Focus
  returns to the row.

## 3. Panel structure

The panel renders the same `RecordView` sections with a `variant: 'panel'` flag. No
second form definition exists; `Meta.layout` and the Controls form layout apply to both.

| Full page | Side panel |
|---|---|
| Header: mark, type, title, number, facts, all actions | Header row: type, `name` in mono, Expand, Close. Then the title (edited in place), status chip, and up to two main actions; everything else in a "…" menu |
| Status flow | Compact flow on one line (current step named, others as dots) |
| Summary strip | Two by two grid |
| Tabs | Same tabs. When they do not fit, the extra ones move to a "More" menu |
| Details sections side by side, fields at width 6 or 12 | Sections stacked and collapsible (state remembered per model), every field full width |
| Child tables (ChildTable) | Compact rows: the first column and the amount, with qty x rate under it. A row opens inline for editing. Totals at the bottom. "Open full table" expands to the full page at that tab |
| Side column: Key fields | Facts under the title and the summary grid |
| Side column: Related | The Related tab |
| Side column: Files | A Files section at the end of Details |
| Side column: Record info | A collapsed "Record info" section at the end of Details |
| Save bar at the bottom of the page | Save bar pinned to the bottom of the panel |
| Banners (locked, voided, revised, filled in from a slip) | Same banners, full width above the tabs |

## 4. Behaviour

- **Unsaved changes.** PR 77's rule applies. Switching panel tabs does not ask. Leaving
  the record asks: another row (click or arrow), Close, Escape, a list page change,
  a filter that removes the row, leaving the list. Expanding keeps the edits.
- **Address.** The list address carries the panel: `/app/invoice?open=01hc1sd9vc1ye`
  plus `&rtab=items` for a tab other than the first (`tab` is already the list's state
  tab). A shared link or a reload opens the same list and record. A record that is not on
  the current page still opens; the list simply has no highlighted row.
- **Back.** Opening a record from a closed panel adds a history entry. Moving to another
  row replaces it, so Back closes the panel instead of stepping through every row
  looked at.
- **New.** With panel mode on, New opens `?open=new` in the panel. Create saves, the
  new row appears at the top of the list, selected, and the address switches to its id.
  Create links with values (`?open=new&customer=...`) follow the same rules as today.
- **Slips.** Opened from the panel exactly as from the page: a modal over everything with
  the panel record as the source. When it is handed in, the panel and the list row reload.
- **Moves and actions** update the panel and the row in place.

## 5. Phone and tablet

- Content area at least 900 px: list and panel side by side.
- Content area below 900 px (tablets, or a narrow window): the panel takes the whole
  content area over the list. The header shows Back to the list instead of Expand.
- Below 768 px (phones): the panel is a full screen view, the sidebar is already hidden,
  the save bar sits above the safe area, and the divider is not shown.

## 6. Themes and accessibility

- Only theme tokens, so light, dark and system work without extra rules. The theme check
  in `npm test` covers the new CSS.
- The panel is a labelled `region` ("Invoice INV-2026-0012"), not a modal: the list stays
  usable. Below 900 px it becomes a full screen view and focus stays inside it.
- Opening moves focus to the panel title; closing returns it to the row. Escape closes
  the innermost thing first (menu, dialog, then the panel).
- The list is a grid with `aria-selected` rows. A polite live region announces "Opened
  INV-2026-0012". The divider has `aria-valuenow`, `aria-valuemin` and `aria-valuemax`.
- `prefers-reduced-motion` turns off the slide and width transitions.

## 7. Implementation outline (after approval)

Python
- `Meta.open_in` (validated; default `"page"`), in `/api/schema` and the schema fixtures.
- Controls list view value gains `open_in`; validation, export and tests.
- Playground: Invoice opens in the panel by default.

Frontend
- `ui/split.ts`: `SplitView` with the resizable divider (in the component Library).
- `app/list-page.ts`: open mode, `open` and `rtab` in the address, selection, keyboard,
  narrowing, the view menu entry.
- `app/record-page.ts`: `RecordView(..., { variant: 'panel' })`; the panel header, compact
  child rows, folding side blocks into Details, store handover on Expand.
- `ui/doc.ts`: `DocFrame` panel variant; tab overflow into "More".
- Tests: opening and switching, the discard prompt rules, address and Back, reload with
  `open`, keyboard, divider width memory, narrow breakpoints, new record in the panel,
  slip from the panel, Expand keeping edits, both themes.

Docs: `docs/ui.md`, `docs/ui-framework.md` (What the model decides, routes),
`docs/controls.md`.

## Decisions for the owner

1. Name of the Python option: `Meta.open_in = "panel"` (proposed) or another name.
2. Should a person's choice override the app and instance default (proposed), or should
   the default be fixed for everyone?
3. Back closes the panel (proposed), or steps back through each row viewed.
4. The switch prompt: Cancel and Discard as today (proposed, matches PR 77), or add
   "Save and open" as a third button.
5. Expand keeps unsaved edits (proposed), or asks first.
