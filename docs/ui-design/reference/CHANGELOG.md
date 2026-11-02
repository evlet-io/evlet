# CRM Workspace changelog

Working artifact: CRM Workspace v2 (updated in place from here on)
Locked baseline: CRM Workspace Concept (v1, approved 01-11-2026, never edited)

## 01-11-2026 (component library)

### Added
- Library (bottom of the sidebar): 113 components drawn from the mockup, each a live demo rendered by the same code the screens use.
- Six groups: Inputs (27), Buttons (10), Elements (17), Display (25), Layout areas (18), Sections (16).
- Four levels marked on every card: Base, Element, Composite, Section. Fields are one BaseField wrapped by 20 field types, each mapped to its model field.
- Library index replaces the sidebar nav while in Library: back arrow, filter, grouped list with level dots. It follows the scroll position and keeps the active item in view; clicking an item scrolls to it.
- Details only in a popup: description, model mapping, properties, variants, what it is built from, which components use it (both clickable), and where it appears.
- Demos are safe: actions that would change real records open the details popup instead. Inputs, switches, segmented controls, the child table, menus, drawer, dialog, toast and search stay live.

## 01-11-2026 (transaction document: Invoice)

### Added
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

## 01-11-2026 (layout page and sidebar fix)

### Changed
- Sidebar top split into two rows: workspace switcher on the first row, app icons on the second. A grey line separates them. The active app tab still opens into the sidebar. An All apps button sits at the end of the app row.
- Tooltips appear instantly on hover and stay until the pointer leaves. They carry a pointer: above an item it points down, below it points up, beside the collapsed sidebar it points left. No tooltips on touch devices.
- Doc view template page removed. Replaced by Doc layout (bottom of sidebar): a live sample record with a switch at the top between Sample data and Placeholders ({title}, {status flow}, {child table} and so on).
- The floating control on Doc layout edits the template for every record. On records it still edits only your own view, and links to Doc layout.
- Block names are now layout terms only: Child table tab (was Items / Branches), Highlight card (was Scorecard / Channels). No record-type labels in the controls.

## 01-11-2026 (doc view update)

### Added
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

## 01-11-2026

### Changed
- Apps moved out of the sidebar list into a small icon row at the bottom of the sidebar, with hover labels and an All apps grid.
- Search window rebuilt: wider, taller input, Recent section with the last 4 searches, Go to quick access grid.
- Quick access can be customized: 1 or 2 rows, add or remove tiles from Modules, Reports and Documents. Document tiles open a list or a new form.
- Keyboard navigation in search now covers the tile grid (arrow keys).

### Fixed
- Collapsing the sidebar hid the expand button. It now stays visible.
- Search window spilled past the screen edge on phones.
