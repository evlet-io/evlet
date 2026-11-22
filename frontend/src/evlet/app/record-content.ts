// The white card of a compact record page (docs/ui.md, Compact record page): the state banner strip, the primary
// area (Main, else the first section, and the Tables no block places), the "more information" rows of the other
// sections, Tables, related records, Html, charts and script blocks, and each Table's grid and row editor. Drawn from
// the record view's shared scope (app/record-scope.ts); app/record-page.ts wires the rest of the view.

import { html, when, type Child } from '../dom';
import { cell, effect, untrack, type Cell } from '../reactive';
import type { Row } from '../model/record';
import type { LayoutBlock } from '../model/schema';
import { Avatar, Button, Icon, IconButton, RecordMark } from '../ui/base';
import { ChildTable, type ChildTableControls } from '../ui/childtable';
import { fieldFor, type FieldLike } from '../ui/fields';
import { flowGrid, Folds } from '../ui/fold';
import { FieldGrid } from '../ui/inline';
import { gridPrefs } from '../ui/table';
import { isStateField, screenModel, sections, stepLabel } from './meta';
import { blockFilters, HtmlContent, MeasureChart, RelatedRecords } from './record-blocks';
import { fieldsSummary, MoreRow, rowsText } from './record-compact';
import { whenVisible } from './related-data';
import type { RecordScope, TableChild } from './record-scope';

/**
 * The page's blocks, in order: the declared layout's (or, without one, every section), then sections it does not
 * place (Other, or one a Controls layout added). The primary area is Main, else the first section (a schema section
 * marked main wins); every Table no block places joins it.
 */
export function compactBlocks(s: RecordScope): LayoutBlock[] {
  const all = sections(s.model);
  const declared = s.layout?.blocks ?? all.map((section): LayoutBlock => ({ kind: 'section', label: section.label }));
  const named = new Set(declared.flatMap(block => block.kind === 'section' ? [block.label] : []));
  const blocks = [...declared, ...all.filter(section => !named.has(section.label)).map((section): LayoutBlock => ({ kind: 'section', label: section.label }))];
  if (!blocks.some(block => (block.kind === 'section' || block.kind === 'table') && block.main)) {
    const marked = all.find(section => section.main)?.label;
    const at = blocks.findIndex(block => block.kind === 'section' && (marked === undefined || block.label === marked));
    if (at >= 0) blocks[at] = { ...(blocks[at] as { kind: 'section'; label: string }), main: true };
  }
  const placed = new Set(blocks.flatMap(block => block.kind === 'table' ? [block.name] : []));
  return [...blocks, ...s.tables.filter(child => !placed.has(child.name)).map((child): LayoutBlock => ({ kind: 'table', name: child.name, main: true }))];
}

// Related records and charts need a saved record; a Table shows unless the side panel hides it.
function blockShown(s: RecordScope, block: LayoutBlock) {
  return block.kind === 'related' || block.kind === 'chart' ? !s.isNew
    : block.kind === 'table' ? s.tables.some(child => child.name === block.name) && (!s.shows || s.shows.tab(block.name)) : true;
}

/** The blocks this record and this view show. */
export function shownBlocks(s: RecordScope): LayoutBlock[] {
  const sectionShown = (label: string) => s.panelSections().some(section => section.label === label);
  return compactBlocks(s).filter(block => block.kind === 'section' ? sectionShown(block.label) : blockShown(s, block));
}

/** A block the model script draws (Main(custom=...) or Section(..., custom=...)); empty without a script. */
const customBlock = (s: RecordScope, block: Extract<LayoutBlock, { kind: 'custom' }>) =>
  html`<div class="ev-script-block" data-block=${block.name}>${s.script ? s.script.mount(block.custom, el => [el]) : null}</div>`;
/** A group of the main zone: open, with a small heading when it has a title (Main(title=...)). */
const mainGrid = (title: string | null, grid: Child) => html`${title ? html`<h3 class="ev-cmp-mh">${title}</h3>` : null}<div class="ev-cmp-grid">${grid}</div>`;
const lockedText = (s: RecordScope) => () => s.model.fields.some(field => (field as any).versioned === false) ? `Locked. Lines can’t change while it is ${stepLabel(s.model, s.stateValue()).toLowerCase()}.` : 'Locked. Lines can’t change now.';
// A Table the record fills itself (derived=True) is read only here, whatever the state.
export const derivedText = () => 'Filled in by the record when it is saved.';

/** A Table's row editor form, laid out as the record itself: the row model's main groups (without one, its first
 * section) as open grids, the rest as "more information" rows; fields are inputs, since a row is saved with the record. */
function rowForm(s: RecordScope, child: { name: string; model?: string }) {
  let groups: ReturnType<typeof sections> = [];
  try { groups = child.model ? sections(screenModel(s.ctx.schema, child.model)) : []; } catch { groups = []; }
  const rowFolds = new Folds(s.ctx.prefs.formSections(child.model ?? child.name), s.isNew);
  return (row: Row, field: (name: string) => FieldLike): Child => {
    const shown = groups.map(group => ({ ...group, fields: group.fields.filter(meta => row.fields.has(meta.name)) })).filter(group => group.fields.length);
    const gridOf = (names: string[], flow?: 'rows' | 'columns') => flowGrid(names.map(name => field(name))
      .map(item => ({ node: fieldFor({ field: item, links: s.ctx.links }), wide: Boolean(item.meta.wide ?? item.meta.type === 'longtext') })), flow);
    if (shown.length < 2) return html`<div class="ev-row-form">${gridOf([...row.fields.keys()].filter(name => !row.field(name).meta.hidden))}</div>`;
    const mains = shown.some(group => group.main) ? shown.filter(group => group.main) : shown.slice(0, 1);
    const rest = shown.filter(group => !mains.includes(group));
    return html`<div class="ev-row-form">${mains.map(group => mainGrid(group.heading ? group.label : null, gridOf(group.fields.map(meta => meta.name), group.flow)))}
      <div class="ev-more">${rest.map((section, position) => {
        const fields = section.fields.map(meta => field(meta.name));
        return MoreRow({ title: section.label, description: section.description, folds: rowFolds, eager: true, fields,
          start: rowFolds.start(section.collapsible ? section.collapsed ?? false : false, position + 1, fields),
          summary: () => fieldsSummary(fields, s.ctx.links), body: () => gridOf(section.fields.map(meta => meta.name), section.flow) });
      })}</div></div>`;
  };
}

// A derived table drops its editing controls; in a "more information" row its note sits in the row's heading instead.
function tablePanel(s: RecordScope, controlsOf: Map<string, ChildTableControls>, child: TableChild, note = true) {
  const { ctx, store, model, look } = s;
  const tableLocked = () => Boolean(child.read_only) || s.locked.get() || !s.canWrite;
  const tableText = child.read_only ? derivedText : lockedText(s);
  return ChildTable({ table: store.table(child.name), title: child.label, icon: 'rows-3', tint: look.tint,
    rowForm: rowForm(s, child), controls: controls => controlsOf.set(child.name, controls), derived: Boolean(child.read_only),
    locked: tableLocked, lockedText: child.read_only && !note ? '' : tableText, links: ctx.links, crumb: () => s.titleText.get(),
    heading: { type: model.label, icon: look.icon, title: () => s.titleText.get(),
      number: s.numberText ?? (s.naming ? () => { const value = store.field('name').value.get(); return value && String(value) !== s.titleText.get() ? String(value) : ''; } : null),
      mark: () => look.person ? Avatar({ name: s.markName, size: 'sm' }) : RecordMark({ name: s.markName, size: 'sm', tint: look.tint }), footer: () => s.saveBarView() as Child },
    prefs: ctx.prefs.cell(`grid.${model.name}.${child.name}`, gridPrefs(store.table(child.name).model.fields)) });
}

/** The selection bar, on a Table's title line while rows are selected (not on a locked record). */
function selectionBar(controls: ChildTableControls | undefined) {
  return controls ? when(() => controls.selected.get() > 0 && controls.editable.get(), () => html`<span class="ev-cmp-sel" role="status">${() => `${controls.selected.get()} selected`}</span>
    ${Button({ label: 'Duplicate', size: 'small', onClick: () => controls.duplicateSelected() })}
    ${Button({ label: 'Delete', variant: 'soft-danger', size: 'small', className: 'ev-cmp-del', onClick: () => controls.deleteSelected() })}`) : null;
}

/** Focus (the table takes the content area; Esc leaves it) and Columns, as subtle square buttons by the title. */
function tableTools(controls: ChildTableControls | undefined) {
  const focusButton = controls ? (() => {
    const button = IconButton({ icon: 'maximize-2', tip: () => controls.focused.get() ? 'Leave focus (Esc)' : 'Focus', size: 'small', active: controls.focused, onClick: () => controls.toggleFocus() });
    effect(() => { const icon = Icon(controls.focused.get() ? 'minimize-2' : 'maximize-2', { size: 'sm' }); untrack(() => button.replaceChildren(icon)); });
    button.tabIndex = -1;
    return button;
  })() : null;
  return controls ? html`<span class="ev-cmp-tools">${focusButton}${(() => {
    const button = IconButton({ icon: 'columns-3', tip: 'Columns', size: 'small', onClick: () => controls.columns(button) });
    button.setAttribute('aria-haspopup', 'dialog');
    // Tab goes from the field before the table straight to its first cell; these two are for the mouse.
    button.tabIndex = -1;
    return button;
  })()}</span>` : null;
}

/** A Table on a compact page: its title and a muted row count (with rows selected: "2 selected", Duplicate and
 * Delete), Focus and Columns on the right, the grid (whose last row adds a row), and a light dashed box while it has
 * no rows.
 * withTitle: the table heads the primary area with its title. Otherwise it sits in a "more information" row, whose
 * heading takes the tools (toolsOut) so the table spans the card's width. */
function compactTable(s: RecordScope, controlsOf: Map<string, ChildTableControls>, child: TableChild, withTitle: boolean, toolsOut?: Cell<Child>) {
  const table = s.store.table(child.name);
  const count = () => table.rows.get().length;
  const canAdd = () => s.canWrite && !s.locked.get() && !child.read_only;
  // An empty table that can take rows shows its header and Add row placeholder, the click that starts it. One that
  // can't (and has no section rows) is a quiet "No … yet" box instead.
  const empty = () => count() === 0 && table.sections.get().length === 0 && !canAdd();
  const grid = tablePanel(s, controlsOf, child, withTitle), controls = controlsOf.get(child.name);
  const tools = tableTools(controls);
  // Hidden while the Table's applies_when or visible_when is false (a save removes rows that do not apply).
  return html`<div class="ev-cmp-table" class:is-empty=${empty} hidden=${() => !table.rules.get().visible} data-table-block=${child.name}>
    ${withTitle ? html`<div class="ev-cmp-th"><h3>${child.label}</h3><span class="muted num ev-cmp-count">${() => String(count())}</span>${selectionBar(controls)}<span class="grow"></span>${tools}</div>`
      : toolsOut ? (toolsOut.set([selectionBar(controls), tools]), null) : html`<div class="ev-cmp-th ev-cmp-th-tools">${selectionBar(controls)}<span class="grow"></span>${tools}</div>`}
    ${when(empty, () => html`<div class="ev-cmp-empty"><span>${`No ${child.label.toLowerCase()} yet.`}</span></div>`)}
    ${grid}</div>` as HTMLElement;
}

/** A "more information" row for one block that is not in the primary area; null for a section with nothing to show. */
function moreRow(s: RecordScope, controlsOf: Map<string, ChildTableControls>, block: LayoutBlock, position: number,
  fieldsOf: (label: string) => FieldLike[], grid: (fields: FieldLike[], flow?: 'rows' | 'columns') => Child): Node | null {
  const { ctx, model, store, folds } = s;
  switch (block.kind) {
    case 'section': {
      const section = sections(model).find(group => group.label === block.label);
      const fields = fieldsOf(block.label);
      if (!fields.length) return null;
      return MoreRow({ title: block.label, description: section?.description, folds, eager: true, fields,
        start: folds.start(section?.collapsible ? section.collapsed ?? false : false, position, fields),
        summary: () => fieldsSummary(fields, ctx.links), body: () => grid(fields, section?.flow) });
    }
    case 'table': {
      const child = s.tables.find(item => item.name === block.name)!;
      const table = store.table(child.name);
      const tools = cell<Child>(null);
      // A TableSection starts closed unless it says otherwise; 'empty' is closed while the table has no rows.
      const collapsed = block.collapsible === false ? false : block.collapsed ?? true;
      const start = folds.start(collapsed === 'empty' ? untrack(() => table.rows.get().length) === 0 : collapsed, position, []);
      // Drawn while closed too, so print shows its rows.
      const more = MoreRow({ title: child.label, folds, start, wide: true, eager: true,
        description: child.read_only ? derivedText() : undefined, tools: () => tools.get(),
        summary: () => rowsText(table.rows.get().length), none: 'No rows', body: () => compactTable(s, controlsOf, child, false, tools) });
      // The whole row hides with its Table's rules.
      effect(() => { more.hidden = !table.rules.get().visible; });
      return more;
    }
    case 'related': {
      // The count for the closed row: one row asked for, with the block's filters, once the row shows.
      const total = cell<number | null>(null);
      const target = ctx.schema.schema.models.find(item => item.name === block.model);
      const count = () => {
        const filters = blockFilters(block, s.blockRecord);
        if (!filters || !target) { total.set(0); return; }
        void ctx.api.list(block.model, { limit: 1, offset: 0, filters }).then(page => total.set((page as { total?: number }).total ?? page.items.length), () => total.set(null));
      };
      const row = MoreRow({ title: block.name ?? block.label, folds, start: false, none: 'Loading…',
        summary: () => total.get() === null ? '' : `${target ? (target as { plural?: string }).plural ?? block.label : block.label}: ${total.get()}`,
        body: () => RelatedRecords(ctx, block, s.blockRecord) as Child });
      whenVisible(row, count);
      return row;
    }
    case 'html': {
      const text = block.html.replace(/<[^>]*>/g, ' ').replace(/\{[a-z_][a-z0-9_]*\}/g, '…').replace(/\s+/g, ' ').trim();
      return MoreRow({ title: block.name ?? 'Notes', folds, start: false, eager: true, summary: () => text, body: () => HtmlContent(ctx, model, block, s.blockRecord) });
    }
    case 'chart':
      return MoreRow({ title: block.name ?? block.label ?? 'Chart', folds, start: false, summary: () => '', none: 'A chart', body: () => MeasureChart(ctx, block, s.blockRecord) });
    case 'custom':
      return MoreRow({ title: block.label, folds, eager: true, start: folds.start(block.collapsible === false ? false : block.collapsed === true, position, []),
        summary: () => '', body: () => customBlock(s, block) });
  }
  return null;
}

/** What the white card holds besides the form: the stage bar and key cards above it, the state banner strip on it and
 * the related records section after more information. */
export interface ContentParts { flow: Child; summary: Child; lifecycle: (() => Child) | null; relatedGroup: () => Child }

/** The stage bar and the key cards above one white card: the state banner strip, the primary area, then more
 * information. */
export function compactContent(s: RecordScope, parts: ContentParts): Child[] {
  const { ctx, model, isNew, view } = s;
  const controlsOf = new Map<string, ChildTableControls>();
  const blocks = shownBlocks(s);
  const groups = s.panelSections(), all = sections(model);
  const fieldsOf = (label: string) => {
    const section = groups.find(group => group.label === label);
    if (!section) return [];
    const metas = isNew ? section.fields.filter(meta => (!meta.read_only || meta.name === model.naming?.field) && !meta.compute && !isStateField(model, meta)) : section.fields;
    return metas.map(meta => view(meta.name));
  };
  const grid = (fields: FieldLike[], flow?: 'rows' | 'columns') => isNew
    ? flowGrid(fields.map(field => ({ node: fieldFor({ field, links: ctx.links }), wide: Boolean(field.meta.wide ?? field.meta.type === 'longtext') })), flow)
    : FieldGrid(fields, { links: ctx.links, flow });
  const mainParts = blocks.filter(block => (block.kind === 'section' || block.kind === 'table' || block.kind === 'custom') && block.main).map(block => block.kind === 'section'
    ? (section => mainGrid(section?.heading ? block.label : null, grid(fieldsOf(block.label), section?.flow)))(all.find(group => group.label === block.label))
    : block.kind === 'custom' ? customBlock(s, block)
    : compactTable(s, controlsOf, s.tables.find(child => child.name === (block as { name: string }).name)!, true));
  const rows: Node[] = [];
  let index = 0;
  for (const block of blocks.filter(item => !('main' in item && item.main))) {
    const row = moreRow(s, controlsOf, block, ++index, fieldsOf, grid);
    if (row) rows.push(row);
  }
  // Related records the layout does not list: a section of its own after more information.
  const relatedGroup = parts.relatedGroup();
  return [parts.flow, parts.summary, html`<div class="ev-cmp-card">${parts.lifecycle ? html`${parts.lifecycle}` : null}
    <section class="ev-cmp-main" aria-label=${model.label}>${mainParts}</section>
    ${rows.length ? html`<div class="ev-more" role="group" aria-labelledby=${`ev-more-${model.name}`}><h2 class="ev-more-label" id=${`ev-more-${model.name}`}>More information</h2>${rows}</div>` : null}
    ${relatedGroup}</div>`];
}
