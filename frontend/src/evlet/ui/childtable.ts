// ChildTable: the lines of a record as an editable grid bound to a record store Table.
//
// Pinned control column and first data column, sticky header and totals, resizable columns,
// a Columns menu (show, hide, reorder, reset), click or Enter to edit a cell, Tab and Shift Tab
// to the next editable cell (wrapping rows), Enter to the cell below, Esc to cancel, arrow keys
// between cells, Alt and an arrow to move a row. Rows open in place with every field, and the
// table can take over the content area (focus view). Rows are virtualized: a 1,000-row table
// keeps only the rows in view in the DOM. It locks with the record.

import { each, html, when, type Child } from '../dom';
import { batch, cell, derive, effect, onCleanup, untrack, type Cell } from '../reactive';
import type { Field, Row, Table } from '../model/record';
import type { FieldSchema } from '../model/schema';
import { Button, Checkbox, Icon, IconTile, Key } from './base';
import { read, textOf, uid, type Prop, type Tint } from './core';
import { BaseField, displayValue, FieldControl, fieldFor, writeValue, type Control, type FieldLike, type LinkSource } from './fields';
import { kindOf } from './kinds';
import { openPopover, toast } from './layers';
import { arrange, byKey, ColumnsMenu, emptyPrefs, pinnedKeys, pinTracker, resizeHandle, virtualWindow, type TablePrefs } from './table';
import { tooltip } from './tooltip';

const ROW = 38, HEADER = 36;
// Under 480 px the control column drops the row number and the first column starts narrower,
// so a phone keeps room for the other columns.
const NARROW = '(max-width: 480px)';
const narrowNow = () => { try { return Boolean(window.matchMedia?.(NARROW)?.matches); } catch { return false; } };
const WIDTHS: Record<string, number> = { text: 200, longtext: 260, link: 200, currency: 140, decimal: 110, integer: 90, percent: 100, date: 120, datetime: 160,
  time: 90, duration: 110, select: 140, boolean: 90, email: 200, url: 200, phone: 150, tags: 200, state: 120, sequence: 150, reference: 180 };
const RICH = new Set(['link', 'state', 'tags', 'sequence', 'boolean']);

export interface ChildColumn { key: string; label: string; width: number; meta: FieldSchema; calculated: boolean; align: 'left' | 'right' }
export interface TotalItem { label: string; value: Prop<string>; grand?: boolean }

/** The record a child table belongs to, for the compact header of its focus view. */
export interface FocusHeading {
  /** The record type ("Employee") and its icon. */
  type: string;
  icon?: string;
  /** The record's mark or avatar, built small. */
  mark?: () => Node;
  title: Prop<string>;
  /** The record number, in mono after the title. */
  number?: Prop<string> | null;
  /** The form's save bar (shown while it has something to say). */
  footer?: () => Child;
}

export interface ChildTableProps {
  table: Table;
  /** Field names to show, in order. Defaults to the line model's visible fields. */
  columns?: string[];
  title?: string;
  icon?: string;
  tint?: Tint;
  /** Locks the lines (for example once the record is issued). */
  locked?: Prop<boolean>;
  /** Why the lines are locked; read when the lock shows, so it follows the record's state. */
  lockedText?: Prop<string>;
  /** Footer values by column; defaults to the parent's sums over this table. */
  totals?: Record<string, FieldLike | Prop<string>>;
  /** Totals under the table (net, VAT, grand total); defaults to the footer sums. */
  summary?: TotalItem[];
  prefs?: Cell<TablePrefs>;
  /** The record's name, shown as a crumb in focus view. */
  crumb?: Prop<string>;
  /** The record shown above the table in focus view, so it still reads as part of the form. */
  heading?: FocusHeading;
  links?: LinkSource | null;
  /** Viewport height to assume before the container is measured. */
  height?: number;
}

type Spot = { key: string; col: string } | null;
const sameSpot = (a: Spot, b: Spot) => a === b || (!!a && !!b && a.key === b.key && a.col === b.col);

/** The parent's calculated sums over every row of a table: { amount: order.total }. A filtered sum
 * (`lines.where(...).sum("amount")`) is not the column's total, so it never takes the footer. */
function parentSums(table: Table): Record<string, Field> {
  const sums: Record<string, Field> = {};
  for (const field of table.owner.fields.values()) {
    const compute = field.meta.compute as any;
    if (compute?.$expr === 'aggregate' && compute.table === table.name && compute.kind === 'sum' && compute.field && !compute.where
      && !(compute.field in sums)) sums[compute.field] = field;
  }
  return sums;
}

/** Fields of the parent calculated only from those sums (total = net + vat): the grand totals under the table. */
function parentGrandTotals(table: Table, sums: Record<string, Field>): Field[] {
  const names = new Set(Object.values(sums).map(field => field.meta.name));
  const refs = (expr: any, found: string[]): string[] => {
    if (!expr || typeof expr !== 'object') return found;
    if (expr.$expr === 'ref') found.push(expr.scope === 'self' ? expr.field : '');
    else if (expr.$expr !== 'literal') Object.values(expr).forEach(value => Array.isArray(value) ? value.forEach(item => refs(item, found)) : refs(value, found));
    return found;
  };
  return [...table.owner.fields.values()].filter(field => {
    const compute = field.meta.compute as any;
    if (!compute || compute.$expr === 'aggregate' || names.has(field.meta.name)) return false;
    const read = refs(compute, []);
    return read.length > 0 && read.every(name => names.has(name));
  });
}

const valueText = (value: FieldLike | Prop<string>): string => {
  if (value && typeof value === 'object' && 'meta' in value) return kindOf(value.meta.type).format(value.value.get(), value.meta);
  return read(value as Prop<string>) ?? '';
};

/** Lines of a record: an editable grid with live totals that locks with the record. */
export function ChildTable(props: ChildTableProps): HTMLElement {
  const { table } = props, model = table.model, record = table.owner.record;
  const prefs = props.prefs ?? cell(emptyPrefs(), { name: `${table.name} (columns)` });
  const metas = new Map(model.fields.map(meta => [meta.name, meta]));
  const allColumns: ChildColumn[] = (props.columns ?? model.fields.filter(meta => !meta.hidden).map(meta => meta.name)).map(name => {
    const meta = metas.get(name);
    if (!meta) throw new Error(`${model.name} has no field ${name}`);
    const calculated = meta.compute !== null && meta.compute !== undefined;
    return { key: name, label: meta.label, width: WIDTHS[meta.type] ?? 160, meta, calculated, align: kindOf(meta.type).align === 'right' ? 'right' : 'left' };
  });
  const columns = derive(() => arrange(allColumns, prefs.get()), { name: `${table.name} (shown columns)` });
  const narrow = cell(narrowNow(), { name: `${table.name} (narrow)` });
  const ctl = () => narrow.get() ? 64 : 84;
  const widthOf = (column: ChildColumn) => prefs.get().widths[column.key]
    ?? (narrow.get() && columns.get()[0]?.key === column.key ? Math.min(column.width, 120) : column.width);
  const totalWidth = derive(() => ctl() + columns.get().reduce((sum, column) => sum + widthOf(column), 0));
  // Pinned columns stay on the left after the row control column (one by default).
  const allKeys = allColumns.map(column => column.key);
  const { pin } = pinTracker(() => columns.get(), () => pinnedKeys(prefs.get(), allKeys, 1), widthOf, ctl);
  const locked = derive(() => Boolean(read(props.locked ?? false)), { name: `${table.name} (locked)` });
  const readOnly = derive(() => locked.get() || !record.ready.get());
  const sums = props.totals ?? parentSums(table);
  const grand = props.totals ? [] : parentGrandTotals(table, sums as Record<string, Field>);
  const summary = props.summary ?? [...Object.entries(sums), ...grand.map(field => [field.meta.name, field] as const)].map(([key, value], index, all) => ({
    label: value && typeof value === 'object' && 'meta' in value ? value.meta.label : allColumns.find(column => column.key === key)?.label ?? key,
    value: () => valueText(value), grand: index === all.length - 1 && all.length > 1,
  }));

  const rows = table.rows;
  const selection = cell(new Set<string>(), { name: `${table.name} (selection)` });
  const editing = cell<Spot>(null, { name: `${table.name} (editing)`, equals: sameSpot });
  const active = cell<Spot>(null, { name: `${table.name} (active cell)`, equals: sameSpot });
  const expanded = cell<string | null>(null, { name: `${table.name} (expanded)` });
  const expandedHeight = cell(0);
  const focused = cell(false, { name: `${table.name} (focus view)` });
  const indexOf = (key: string) => untrack(() => rows.get()).findIndex(row => row.key === key);
  const view = virtualWindow({
    count: () => rows.get().length, rowHeight: ROW, header: HEADER, fallback: props.height ?? 560,
    extra: () => { const key = expanded.get(); if (!key) return null; const index = rows.get().findIndex(row => row.key === key); return index < 0 ? null : { index, height: expandedHeight.get() }; },
  });
  const visible = derive(() => { const { start, end } = view.range.get(); return rows.get().slice(start, end); }, { name: `${table.name} (visible rows)` });
  let scroller!: HTMLElement, grid!: HTMLElement, control: Control | null = null, before: unknown = null;
  // The text the cell's input started with, so a cell passed through untouched never traps focus.
  let startText: string | null = null;
  // The cell a choice (dropdown, link, picker) just committed: Enter there goes to the cell below.
  let landed: Spot = null;

  // ---------- editing ----------

  const editable = (row: Row, col: string) => {
    const column = allColumns.find(item => item.key === col);
    return Boolean(column) && !column!.calculated && !untrack(() => readOnly.get()) && untrack(() => row.field(col).editable.get());
  };
  const cellAt = (spot: NonNullable<Spot>) => {
    const row = byKey(scroller, 'tr', spot.key);
    return row ? [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === spot.col) ?? null : null;
  };
  const focusCell = (spot: Spot) => { if (spot) cellAt(spot)?.focus(); };
  const reveal = (key: string) => { const index = indexOf(key); if (index >= 0) view.reveal(scroller, index); };
  const startEdit = (spot: NonNullable<Spot>, typed?: string) => {
    const row = untrack(() => rows.get()).find(item => item.key === spot.key);
    if (!row || !editable(row, spot.col)) return false;
    reveal(spot.key);
    before = untrack(() => row.field(spot.col).value.get());
    landed = null;
    batch(() => { active.set(spot); editing.set(spot); });
    if (control) {
      control.focus();
      const input = control.element.matches('input') ? control.element as HTMLInputElement : control.element.querySelector('input');
      startText = textIn();
      if (typed !== undefined && control.type) control.type(typed);
      else if (typed !== undefined && input) { input.value = typed; input.setSelectionRange?.(typed.length, typed.length); }
    }
    return true;
  };
  const stopEdit = (focus = true) => {
    const spot = editing.peek();
    editing.set(null);
    control = null;
    startText = null;
    if (focus && spot) focusCell(spot);
  };
  const textIn = () => {
    const input = control?.element.matches('input, textarea') ? control.element as HTMLInputElement : control?.element.querySelector<HTMLInputElement>('input, textarea');
    return input ? input.value : null;
  };
  const editedField = () => {
    const spot = editing.peek();
    return spot ? untrack(() => rows.get()).find(item => item.key === spot.key)?.field(spot.col) ?? null : null;
  };
  // Commits the cell being edited. A cell the user passed through without changing it (an
  // empty required cell, say) never blocks moving on: the record checks it when it is saved.
  const commit = (): boolean => {
    if (!control || control.commit()) return true;
    const field = editedField();
    if (startText !== null && textIn() === startText && field
      && JSON.stringify(untrack(() => field.value.get()) ?? null) === JSON.stringify(before ?? null)) { field.error.set(null); return true; }
    return false;
  };
  // A cell that could not be committed keeps editing, and keeps the focus.
  const refocus = () => { if (control && !control.element.contains(document.activeElement)) control.focus(); };
  // The first control after the table, for Tab past its last cell.
  const after = () => grid.querySelector<HTMLButtonElement>('.g-under .g-add:not(:disabled)');
  const cancel = () => {
    const spot = editing.peek();
    if (!spot) return;
    const row = untrack(() => rows.get()).find(item => item.key === spot.key);
    const field = row?.field(spot.col);
    if (field && JSON.stringify(untrack(() => field.value.get()) ?? null) !== JSON.stringify(before ?? null)) {
      try { field.set(before); } catch { /* keep the record's value */ }
    }
    field?.error.set(null);
    stopEdit();
  };
  // The next editable cell after (or before) a spot, wrapping rows.
  const step = (spot: NonNullable<Spot>, direction: 1 | -1): Spot => {
    const list = untrack(() => rows.get()), cols = untrack(() => columns.get()).map(column => column.key);
    let r = list.findIndex(row => row.key === spot.key), c = cols.indexOf(spot.col);
    for (let guard = 0; guard < list.length * cols.length; guard++) {
      c += direction;
      if (c >= cols.length) { c = 0; r++; }
      if (c < 0) { c = cols.length - 1; r--; }
      if (r < 0 || r >= list.length) return null;
      if (editable(list[r], cols[c])) return { key: list[r].key, col: cols[c] };
    }
    return null;
  };
  const below = (spot: NonNullable<Spot>, direction: 1 | -1 = 1): Spot => {
    const list = untrack(() => rows.get()), at = list.findIndex(row => row.key === spot.key) + direction;
    return at >= 0 && at < list.length ? { key: list[at].key, col: spot.col } : null;
  };
  const moveActive = (spot: Spot) => {
    if (!spot) return;
    reveal(spot.key);
    active.set(spot);
    focusCell(spot);
  };

  // ---------- rows ----------

  // A row's own values and its rows of rows, as new rows for table.add().
  const copyValues = (row: Row): Record<string, unknown> => {
    const values: Record<string, unknown> = Object.fromEntries([...row.fields.values()].filter(field => !field.calculated).map(field => [field.name, untrack(() => field.value.get())]));
    if (row.tables.size) values.children = Object.fromEntries([...row.tables].map(([name, nested]) => [name, untrack(() => nested.rows.get()).map(copyValues)]));
    return values;
  };
  // Why rows can't change now, or null.
  const blocked = () => readOnly.peek() ? (locked.peek() ? 'The lines are locked, so the rows can\u2019t come back.' : 'The record is still loading. Try Undo again in a moment.')
    : record.saving.peek() ? 'The record is being saved. Try Undo again in a moment.' : null;
  const add = () => {
    if (readOnly.peek()) return;
    const row = table.add();
    const first = untrack(() => columns.get()).find(column => editable(row, column.key));
    reveal(row.key);
    if (first) startEdit({ key: row.key, col: first.key });
  };
  const duplicate = (list: Row[]) => {
    if (readOnly.peek() || !list.length) return;
    batch(() => {
      for (const row of list) {
        const copy = table.add(copyValues(row));
        table.move(copy, untrack(() => rows.get()).indexOf(row) + 1);
      }
    });
    toast(list.length === 1 ? 'Duplicated 1 row' : `Duplicated ${list.length} rows`, { icon: 'copy' });
  };
  const remove = (list: Row[]) => {
    if (readOnly.peek() || !list.length) return;
    const positions = list.map(row => ({ at: untrack(() => rows.get()).indexOf(row), row })).sort((a, b) => a.at - b.at);
    batch(() => {
      for (const row of list) table.remove(row);
      selection.set(new Set());
      if (list.some(row => row.key === expanded.peek())) expanded.set(null);
    });
    toast(list.length === 1 ? 'Deleted 1 row' : `Deleted ${list.length} rows`, {
      icon: 'trash-2',
      // The same rows come back, with their ids and rows of rows, where they were.
      undo: () => {
        const why = blocked();
        if (why) { toast(why, { icon: 'info' }); return; }
        try { for (const item of positions) table.restore(item.row, item.at); }
        catch (error) { toast(error instanceof Error ? error.message : 'The rows could not come back.', { icon: 'info' }); }
      },
    });
  };
  const moveRow = (row: Row, direction: 1 | -1) => {
    if (readOnly.peek()) return;
    const at = untrack(() => rows.get()).indexOf(row), to = at + direction;
    if (to < 0 || to >= untrack(() => rows.get()).length) return;
    table.move(row, to);
    reveal(row.key);
  };
  const selectedRows = () => rows.get().filter(row => selection.get().has(row.key));
  const toggle = (key: string, on: boolean) => { const next = new Set(selection.peek()); if (on) next.add(key); else next.delete(key); selection.set(next); };

  // ---------- views ----------

  const pinStyle = (column: () => ChildColumn) => () => { const at = pin(column().key).get(); return at ? `${at.left}px` : null; };
  const isPinned = (column: () => ChildColumn) => () => Boolean(pin(column().key).get());
  const isLastPin = (column: () => ChildColumn) => () => Boolean(pin(column().key).get()?.last);

  const header = (column: () => ChildColumn) => {
    const th = html`<th scope="col" role="columnheader" data-key=${column().key} class=${column().align === 'right' ? 'r' : ''} class:stk=${isPinned(column)} class:s2=${isLastPin(column)}
      style:left=${pinStyle(column)}><span>${column().label}</span>${column().calculated ? Icon('lock', { size: 'xs', className: 'g-lk', label: 'Calculated' }) : null}</th>` as HTMLElement;
    th.append(resizeHandle(column().key, prefs, { current: () => th.offsetWidth || widthOf(column()), host: () => grid }));
    return th;
  };

  const cellView = (row: Row, column: () => ChildColumn, rowEdit: () => string | null, rowActive: () => string | null, rowSpot: () => string | null) => {
    const key = column().key, field = row.field(key), meta = field.meta, kind = kindOf(meta.type);
    const canEdit = () => !column().calculated && !readOnly.get() && field.editable.get();
    const display = RICH.has(meta.type) ? () => displayValue(field, { links: props.links, empty: '' }) : () => textOf(() => kind.format(field.value.get(), meta));
    const td = html`<td role="gridcell" data-col=${key} class=${`num${column().align === 'right' ? ' r' : ''}`} class:ed=${canEdit} class:calc=${() => !canEdit()}
      class:stk=${isPinned(column)} class:s2=${isLastPin(column)} style:left=${pinStyle(column)} class:editing=${() => rowEdit() === key} class:active=${() => rowSpot() === key}
      class:invalid=${() => Boolean(field.error.get())} class:noted=${() => !field.error.get() && Boolean(field.note.get())} aria-invalid=${() => field.error.get() ? 'true' : null} aria-readonly=${() => canEdit() ? null : 'true'}
      tabindex=${() => rowActive() === key ? '0' : '-1'}>${when(() => rowEdit() === key, () => {
        control = FieldControl(field, {
          commitOn: 'manual', links: props.links, label: `${meta.label}, row ${indexOf(row.key) + 1}`,
          // A choice commits the cell and returns the focus to it, ready for Tab or Enter.
          onCommit: !kind.control || kind.control === 'textarea' ? undefined : () => { const spot = editing.peek(); stopEdit(); landed = spot; },
        });
        return control.element;
      }, display)}</td>` as HTMLElement;
    tooltip(td, () => field.error.get() ?? field.note.get());
    return td;
  };

  const expandPanel = (row: Row) => {
    const index = () => rows.get().indexOf(row);
    const title = () => { const first = columns.get()[0]; return first ? kindOf(first.meta.type).format(row.field(first.key).value.get(), first.meta) || 'New row' : 'New row'; };
    const panel = html`<tr class="g-xrow"><td colspan=${() => String(columns.get().length + 1)}><div class="g-x" role="region" aria-label=${() => `Row ${index() + 1} details`}>
      <div class="g-x-h"><span class="g-x-n num">${() => `Row ${index() + 1} of ${rows.get().length}`}</span><b>${title}</b><span class="grow"></span>
        ${Button({ icon: 'arrow-up', variant: 'ghost', size: 'small', tip: 'Previous row', disabled: () => index() <= 0, onClick: () => { const list = rows.peek(); expanded.set(list[index() - 1].key); } })}
        ${Button({ icon: 'arrow-down', variant: 'ghost', size: 'small', tip: 'Next row', disabled: () => index() >= rows.get().length - 1, onClick: () => { const list = rows.peek(); expanded.set(list[index() + 1].key); } })}
        ${when(() => !readOnly.get(), () => html`<span class="sep"></span>
          ${Button({ label: 'Move up', variant: 'ghost', size: 'small', disabled: () => index() <= 0, onClick: () => moveRow(row, -1) })}
          ${Button({ label: 'Move down', variant: 'ghost', size: 'small', disabled: () => index() >= rows.get().length - 1, onClick: () => moveRow(row, 1) })}
          ${Button({ icon: 'copy', label: 'Duplicate', variant: 'ghost', size: 'small', onClick: () => duplicate([row]) })}
          ${Button({ icon: 'trash-2', label: 'Delete', variant: 'soft-danger', size: 'small', onClick: () => remove([row]) })}`)}
        ${Button({ label: 'Done', size: 'small', onClick: () => { expanded.set(null); queueMicrotask(() => byKey(scroller, 'tr', row.key)?.querySelector<HTMLElement>('.g-exp')?.focus()); } })}</div>
      <div class="g-x-f">${allColumns.map(column => html`<div class=${column.meta.type === 'longtext' ? 'wide' : ''}>${BaseField({ field: row.field(column.key), links: props.links })}${() => prefs.get().hidden.includes(column.key)
        ? html`<span class="muted g-x-hid">Hidden column</span>` : null}</div>`)}</div>
      ${[...row.tables.values()].map(nested => html`<div class="g-x-nested">${ChildTable({ table: nested, locked: props.locked, links: props.links, height: 228,
        lockedText: props.lockedText, icon: 'rows-3', tint: props.tint })}</div>`)}</div></td></tr>` as HTMLElement;
    queueMicrotask(() => expandedHeight.set(panel.getBoundingClientRect().height));
    return panel;
  };

  const rowView = (item: () => Row, index: () => number) => {
    const row = item(), number = () => view.range.get().start + index() + 1;
    const rowEdit = derive(() => editing.get()?.key === row.key ? editing.get()!.col : null);
    const rowSpot = derive(() => { const spot = active.get(); return spot?.key === row.key ? spot.col : null; });
    // The one cell Tab reaches from outside the grid (roving tabindex): the active cell, or the first.
    const rowActive = derive(() => active.get() ? rowSpot.get() : number() === 1 ? columns.get()[0]?.key ?? null : null);
    const open = () => expanded.get() === row.key;
    const tr = html`<tr role="row" data-key=${row.key} class:sel=${() => selection.get().has(row.key)} class:open=${open}
      aria-rowindex=${() => String(number() + 1)} aria-selected=${() => String(selection.get().has(row.key))}>
      <td class="g-ctl stk" style="left:0" role="gridcell"><div class="g-ci">${Checkbox({ checked: () => selection.get().has(row.key), onChange: on => toggle(row.key, on), ariaLabel: () => `Select row ${number()}` })}
        <button type="button" class="g-exp" aria-expanded=${() => String(open())} aria-label=${() => `${open() ? 'Close' : 'Open'} row ${number()}`}
          on:click=${() => expanded.set(open() ? null : row.key)}><span class="num">${() => String(number())}</span>${Icon('chevron-right', { size: 'xs' })}</button></div></td>
      ${each(columns, column => column.key, column => cellView(row, column, () => rowEdit.get(), () => rowActive.get(), () => rowSpot.get()))}</tr>`;
    return [tr, when(open, () => expandPanel(row))];
  };

  const columnsMenu = (anchor: HTMLElement) => openPopover(anchor, () => ColumnsMenu({ columns: allColumns, prefs, pinned: 1,
    widthOf: key => widthOf(allColumns.find(column => column.key === key)!), viewWidth: () => scroller.clientWidth - ctl() }),
  { role: 'dialog', label: 'Columns', className: 'cols-pop' });

  const enterFocus = () => {
    const container = grid.closest('.view') ?? document.querySelector('.view');
    const box = container?.getBoundingClientRect();
    grid.style.top = `${box?.top ?? 0}px`;
    grid.style.left = `${box?.left ?? 0}px`;
    focused.set(true);
  };
  const leaveFocus = () => { focused.set(false); grid.style.top = ''; grid.style.left = ''; };

  // Focus view keeps the record's header above the table (it does not scroll with the rows).
  const focusHead = (heading: FocusHeading) => html`<header class="g-rec" aria-label=${() => `${heading.type} ${read(heading.title) ?? ''}`}>
    ${heading.mark ? html`<span class="g-rec-mark">${heading.mark()}</span>` : null}
    <span class="g-rec-type">${heading.icon ? Icon(heading.icon, { size: 'xs' }) : null}${heading.type}</span>
    <b class="g-rec-title">${heading.title}</b>${heading.number ? html`<span class="g-rec-no mono">${heading.number}</span>` : null}
    <span class="grow"></span>${Button({ icon: 'arrow-left', label: 'Back to form', variant: 'ghost', size: 'small', hideLabelOnPhone: true, onClick: () => { leaveFocus(); grid.querySelector<HTMLElement>('.g-bar [aria-pressed]')?.focus(); } })}</header>`;

  const focusButton = () => {
    const button = html`<button type="button" class="btn sm" class:primary=${focused} aria-pressed=${() => String(focused.get())}
      on:click=${() => focused.peek() ? leaveFocus() : enterFocus()}>${() => Icon(focused.get() ? 'minimize-2' : 'maximize-2', { size: 'sm' })}<span class="hide-sm">${() => focused.get() ? 'Exit focus' : 'Focus'}</span></button>` as HTMLElement;
    tooltip(button, () => focused.get() ? 'Back to record (Esc)' : 'Focus view');
    return button;
  };
  const titleId = uid('ct');
  grid = html`<div class="grid" class:max=${focused} data-table=${table.name}>
    ${props.heading ? when(focused, () => focusHead(props.heading!)) : null}
    <div class="g-bar">
      ${props.heading ? null : when(focused, () => html`<span class="g-crumb">${props.crumb ?? ''}${Icon('chevron-right', { size: 'xs' })}</span>`)}
      <div class="g-t" id=${titleId}>${IconTile(props.icon ?? 'table-2', { tint: props.tint ?? 'blue', size: 26 })}${props.title ?? table.meta.label}<span class="n num" aria-label=${() => `${rows.get().length} rows`}>${() => String(rows.get().length)}</span></div>
      ${when(() => selection.get().size > 0 && !readOnly.get(), () => html`<span class="g-selinfo" role="status">${() => `${selection.get().size} selected`}</span>
        ${Button({ icon: 'copy', label: 'Duplicate', variant: 'ghost', size: 'small', onClick: () => duplicate(selectedRows()) })}
        ${Button({ icon: 'trash-2', label: 'Delete', variant: 'soft-danger', size: 'small', onClick: () => remove(selectedRows()) })}`)}
      <span class="grow"></span>
      ${(() => { const button = Button({ icon: 'columns-3', label: 'Columns', size: 'small', hideLabelOnPhone: true, onClick: () => columnsMenu(button) }); button.setAttribute('aria-haspopup', 'dialog'); return button; })()}
      ${focusButton()}
    </div>
    <div class="g-scroll" tabindex="-1">
      <table class="g" role="grid" aria-labelledby=${titleId} aria-rowcount=${() => String(rows.get().length + 1)} aria-colcount=${() => String(columns.get().length + 1)}
        aria-readonly=${() => readOnly.get() ? 'true' : null} style:width=${() => `${totalWidth.get()}px`}>
        <colgroup><col style:width=${() => `${ctl()}px`}>${each(columns, column => column.key, column => html`<col style:width=${() => `${widthOf(column())}px`}>`)}</colgroup>
        <thead><tr role="row" aria-rowindex="1"><th class="g-ctl stk" style="left:0" scope="col">${Checkbox({ checked: () => rows.get().length > 0 && selectedRows().length === rows.get().length,
          mixed: () => selectedRows().length > 0 && selectedRows().length < rows.get().length, ariaLabel: 'Select all rows',
          onChange: on => selection.set(on ? new Set(rows.peek().map(row => row.key)) : new Set()) })}</th>${each(columns, column => column.key, header)}</tr></thead>
        <tbody>
          <tr class="spacer" aria-hidden="true"><td colspan=${() => String(columns.get().length + 1)} style="padding:0;border:0" style:height=${() => `${view.range.get().before}px`}></td></tr>
          ${each(visible, row => row.key, rowView, { fallback: () => html`<tr><td colspan=${() => String(columns.get().length + 1)} class="g-empty"><div class="g-empty-in">${Icon(props.icon ?? 'table-2', { size: 'lg', className: 'muted' })}<span>No rows yet</span>${when(() => !readOnly.get(),
            () => Button({ icon: 'plus', label: 'Add the first row', variant: 'primary', size: 'small', onClick: add }))}</div></td></tr>` })}
          <tr class="spacer" aria-hidden="true"><td colspan=${() => String(columns.get().length + 1)} style="padding:0;border:0" style:height=${() => `${view.range.get().after}px`}></td></tr>
        </tbody>
        ${when(() => rows.get().length > 0 && Object.keys(sums).length > 0, () => html`<tfoot><tr role="row"><td class="g-ctl stk" style="left:0"></td>${each(columns, column => column.key, (column, index) => html`<td
          class=${column().align === 'right' ? 'r num' : 'num'} class:stk=${isPinned(column)} class:s2=${isLastPin(column)} style:left=${pinStyle(column)}>${() => sums[column().key]
            ? valueText(sums[column().key]) : index() === 0 ? `Total · ${rows.get().length} ${rows.get().length === 1 ? 'line' : 'lines'}` : ''}</td>`)}</tr></tfoot>`)}
      </table>
    </div>
    <div class="g-under">${when(locked, () => html`<span class="g-lock">${Icon('lock', { size: 'xs' })}${() => read(props.lockedText ?? 'Locked. Lines can’t change after the record is issued.')}</span>`,
      () => html`${Button({ icon: 'plus', label: 'Add row', size: 'small', className: 'g-add', disabled: () => !record.ready.get(), onClick: add })}<span class="muted hide-sm">${Key('Tab')} next cell · ${Key('Enter')} cell below · ${Key('Esc')} cancel</span>`)}
      <span class="grow"></span>${summary.length ? html`<div class="g-sum">${summary.map(item => html`<div class:gt=${Boolean(item.grand)}><span>${item.label}</span><b class="num">${item.value}</b></div>`)}</div>` : null}</div>
    ${props.heading?.footer ? when(focused, () => html`<div class="g-foot">${props.heading!.footer!()}</div>`) : null}
  </div>` as HTMLElement;
  scroller = grid.querySelector('.g-scroll') as HTMLElement;
  view.attach(scroller);
  const media = (() => { try { return window.matchMedia?.(NARROW) ?? null; } catch { return null; } })();
  const onMedia = () => narrow.set(narrowNow());
  media?.addEventListener?.('change', onMedia);
  onCleanup(() => media?.removeEventListener?.('change', onMedia));
  const width = () => grid.style.setProperty('--gw', `${scroller.clientWidth || 600}px`);
  width();
  scroller.addEventListener('scroll', () => grid.classList.toggle('scrolled', scroller.scrollLeft > 0), { passive: true });

  // ---------- keyboard ----------

  grid.addEventListener('keydown', event => {
    const td = (event.target as Element).closest?.('td[data-col]') as HTMLElement | null;
    const tr = td?.closest('tr[data-key]') as HTMLElement | null;
    const spot: Spot = td && tr ? { key: tr.dataset.key!, col: td.dataset.col! } : null;
    const editingNow = editing.peek(), chosen = landed;
    landed = null;
    if (editingNow) {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); return; }
      if (event.key === 'Tab') {
        event.preventDefault();
        if (!commit()) { refocus(); return; }
        const next = step(editingNow, event.shiftKey ? -1 : 1);
        // Past the last cell, Tab leaves the table for Add row.
        const out = !next && !event.shiftKey ? after() : null;
        stopEdit(!next && !out);
        if (next) startEdit(next);
        else out?.focus();
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && !(event.target as Element).matches('textarea')) {
        event.preventDefault();
        if (!commit()) { refocus(); return; }
        const next = below(editingNow);
        stopEdit(!next);
        if (next && !startEdit(next)) moveActive(next);
      }
      return;
    }
    if (event.key === 'Escape' && focused.peek()) { event.preventDefault(); event.stopPropagation(); leaveFocus(); return; }
    if (!spot) return;
    const row = untrack(() => rows.get()).find(item => item.key === spot.key)!;
    const cols = untrack(() => columns.get()).map(column => column.key), at = cols.indexOf(spot.col);
    if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) { event.preventDefault(); moveRow(row, event.key === 'ArrowUp' ? -1 : 1); queueMicrotask(() => moveActive(spot)); return; }
    const moves: Record<string, Spot> = {
      ArrowRight: at < cols.length - 1 ? { key: spot.key, col: cols[at + 1] } : null,
      ArrowLeft: at > 0 ? { key: spot.key, col: cols[at - 1] } : null,
      ArrowDown: below(spot), ArrowUp: below(spot, -1),
      Home: { key: spot.key, col: cols[0] }, End: { key: spot.key, col: cols[cols.length - 1] },
    };
    if (event.key in moves) { event.preventDefault(); moveActive(moves[event.key]); return; }
    // Tab and Shift Tab edit the next editable cell; past the last one the browser moves on.
    if (event.key === 'Tab' && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const next = step(spot, event.shiftKey ? -1 : 1);
      if (next) { event.preventDefault(); startEdit(next); }
      return;
    }
    // Enter on a cell a choice just committed goes to the cell below, as it does while editing.
    if (event.key === 'Enter' && sameSpot(chosen, spot)) {
      event.preventDefault();
      const next = below(spot);
      if (next && !startEdit(next)) moveActive(next);
      return;
    }
    if (event.key === 'Enter' || event.key === 'F2') { event.preventDefault(); startEdit(spot); return; }
    if ((event.key === 'Delete' || event.key === 'Backspace') && editable(row, spot.col)) { event.preventDefault(); writeValue(row.field(spot.col), null); return; }
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey && event.key !== ' ') {
      const kind = kindOf(row.field(spot.col).meta.type);
      if (!kind.control || kind.control === 'textarea' || kind.control === 'link') { event.preventDefault(); startEdit(spot, event.key); }
    }
  });
  grid.addEventListener('click', event => {
    const td = (event.target as Element).closest?.('td[data-col]') as HTMLElement | null;
    const tr = td?.closest('tr[data-key]') as HTMLElement | null;
    if (!td || !tr || td.classList.contains('editing')) return;
    const spot = { key: tr.dataset.key!, col: td.dataset.col! };
    landed = null;
    if (editing.peek()) { if (!commit()) { refocus(); return; } stopEdit(false); }
    if (!startEdit(spot)) moveActive(spot);
  });
  // A cell that takes the focus (a click, a tap) becomes the active cell, so the active cell and
  // the focus never part. While a cell is being edited, the click that follows decides.
  grid.addEventListener('focusin', event => {
    const td = event.target as HTMLElement;
    if (editing.peek() || !td.matches?.('td[data-col]')) return;
    const tr = td.closest('tr[data-key]') as HTMLElement | null;
    if (tr) active.set({ key: tr.dataset.key!, col: td.dataset.col! });
  });
  // Leaving the cell being edited commits it (a popover opened from the cell does not count).
  grid.addEventListener('focusout', event => {
    const spot = editing.peek(), next = event.relatedTarget as Element | null;
    if (!spot || (next && (cellAt(spot)?.contains(next) || next.closest?.('.pop')))) return;
    queueMicrotask(() => {
      if (!sameSpot(editing.peek(), spot)) return;
      const now = document.activeElement;
      if (now && (cellAt(spot)?.contains(now) || now.closest?.('.pop'))) return;
      if (commit()) stopEdit(false);
      // A cell that can't be left keeps the focus while it stays in the table.
      else if (now && grid.contains(now)) refocus();
    });
  });
  // A row that disappears (removed, or the record reloaded) cannot stay selected or open.
  effect(() => {
    const keys = new Set(rows.get().map(row => row.key));
    untrack(() => {
      if ([...selection.peek()].some(key => !keys.has(key))) selection.set(new Set([...selection.peek()].filter(key => keys.has(key))));
      if (expanded.peek() && !keys.has(expanded.peek()!)) expanded.set(null);
      const spot = editing.peek();
      if (spot && !keys.has(spot.key)) { editing.set(null); control = null; }
    });
  });
  // A locked table stops editing.
  effect(() => { if (readOnly.get() && untrack(() => editing.get())) untrack(() => { editing.set(null); control = null; }); });
  return grid;
}


// ---------- compact rows ----------

export interface ChildRowsProps {
  table: Table;
  title: string;
  locked?: Prop<boolean>;
  lockedText?: Prop<string>;
  links?: LinkSource | null;
  /** Totals under the rows: the parent's sums over this table. */
  totals?: Array<{ label: string; value: Prop<string> }>;
  /** Show the full grid (the record page at this table's tab). */
  onOpenFull?: () => void;
}

/**
 * A child table for a narrow column (a list's side panel): one line per row with its first value
 * and its last amount, the other numbers under it, edited in place when the row is opened.
 */
export function ChildRows(props: ChildRowsProps): HTMLElement {
  const { table } = props, record = table.owner.record;
  // The link to the parent is the same on every row, so it is not shown.
  const metas = table.model.fields.filter(meta => !meta.hidden && meta.name !== table.meta.link);
  const right = metas.filter(meta => kindOf(meta.type).align === 'right');
  const lead = metas.find(meta => kindOf(meta.type).align !== 'right') ?? metas[0];
  const amount = right[right.length - 1] ?? null;
  const others = right.filter(meta => meta !== amount).slice(0, 2);
  const locked = derive(() => Boolean(read(props.locked ?? false)) || !record.ready.get());
  const open = cell<string | null>(null, { name: `${table.name} (open row)` });
  const id = uid('rows');

  const add = () => {
    try { const row = table.add(); open.set(row.key); } catch (error) { toast((error as Error).message, { icon: 'info' }); }
  };
  const remove = (row: Row) => {
    try { table.remove(row); if (open.peek() === row.key) open.set(null); } catch (error) { toast((error as Error).message, { icon: 'info' }); }
  };
  const value = (row: Row, meta: FieldSchema) => displayValue(row.field(meta.name), { links: props.links, empty: '' });
  const line = (row: () => Row) => {
    const key = () => row().key, expanded = () => open.get() === key();
    return html`<li class="crow" class:open=${expanded} data-key=${key}>
      <button type="button" class="crow-h" aria-expanded=${() => String(expanded())} aria-controls=${() => `${id}-${key()}`}
        on:click=${() => open.set(expanded() ? null : key())}>
        <span class="grow"><b>${lead ? value(row(), lead) : key}</b>
          ${others.length ? html`<span class="crow-sub">${others.map((meta, index) => [index ? ' · ' : '', `${meta.label} `, value(row(), meta)])}</span>` : null}</span>
        ${amount ? html`<span class="crow-amt num">${value(row(), amount)}</span>` : null}${Icon('chevron-down', { size: 'xs', className: 'crow-chev' })}</button>
      ${when(expanded, () => html`<div class="crow-ed" id=${`${id}-${key()}`}>
        ${() => locked.get()
          ? html`<dl class="fields">${metas.map(meta => [html`<dt>${meta.label}</dt>`, html`<dd>${value(row(), meta)}</dd>`])}</dl>`
          : [...metas.map(meta => fieldFor({ field: row().field(meta.name), links: props.links })),
            html`<div class="crow-actions">${Button({ label: 'Remove row', icon: 'trash-2', size: 'small', variant: 'ghost', onClick: () => remove(row()) })}</div>`]}</div>`)}</li>`;
  };
  return html`<section class="crows" aria-label=${props.title}>
    <div class="crows-h"><h3>${() => { const count = table.rows.get().length; return `${count} ${count === 1 ? 'row' : 'rows'}`; }}</h3>
      ${when(() => !locked.get(), () => Button({ label: 'Add row', icon: 'plus', size: 'small', onClick: add }))}
      ${props.onOpenFull ? Button({ label: 'Open full table', icon: 'maximize-2', size: 'small', variant: 'ghost', onClick: props.onOpenFull }) : null}</div>
    ${when(() => Boolean(read(props.locked ?? false)) && Boolean(props.lockedText), () => html`<p class="muted crows-lock">${Icon('lock', { size: 'xs' })}${() => read(props.lockedText!)}</p>`)}
    <ul class="crows-l">${each(table.rows, row => row.key, line, { fallback: () => html`<li class="muted crows-empty">No ${props.title.toLowerCase()} yet.</li>` })}</ul>
    ${props.totals?.length ? html`<dl class="crows-t">${props.totals.map(total => [html`<dt>${total.label}</dt>`, html`<dd class="num">${() => read(total.value)}</dd>`])}</dl>` : null}</section>` as HTMLElement;
}
