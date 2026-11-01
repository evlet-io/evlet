// ChildTable: the lines of a record as an editable grid bound to a record store Table.
//
// Pinned control column and first data column, sticky header and totals, resizable columns,
// a Columns menu (show, hide, reorder, reset), click or Enter to edit a cell, Tab and Shift Tab
// to the next editable cell (wrapping rows), Enter to the cell below, Esc to cancel, arrow keys
// between cells, Alt and an arrow to move a row. Rows open in place with every field, and the
// table can take over the content area (focus view). Rows are virtualized: a 1,000-row table
// keeps only the rows in view in the DOM. It locks with the record.

import { each, html, when } from '../dom';
import { batch, cell, derive, effect, untrack, type Cell } from '../reactive';
import type { Field, Row, Table } from '../model/record';
import type { FieldSchema } from '../model/schema';
import { Button, Checkbox, Icon, IconButton, IconTile, Key } from './base';
import { read, textOf, uid, type Prop, type Tint } from './core';
import { BaseField, displayValue, FieldControl, writeValue, type Control, type FieldLike, type LinkSource } from './fields';
import { kindOf } from './kinds';
import { openPopover, toast } from './layers';
import { arrange, byKey, emptyPrefs, fullOrder, moveColumn, resizeHandle, virtualWindow, type TablePrefs } from './table';
import { tooltip } from './tooltip';

const CTL = 84, ROW = 38, HEADER = 36;
const WIDTHS: Record<string, number> = { text: 200, longtext: 260, link: 200, currency: 140, decimal: 110, integer: 90, percent: 100, date: 120, datetime: 160,
  time: 90, duration: 110, select: 140, boolean: 90, email: 200, url: 200, phone: 150, tags: 200, state: 120, sequence: 150, reference: 180 };
const RICH = new Set(['link', 'state', 'tags', 'sequence', 'boolean']);

export interface ChildColumn { key: string; label: string; width: number; meta: FieldSchema; calculated: boolean; align: 'left' | 'right' }
export interface TotalItem { label: string; value: Prop<string>; grand?: boolean }

export interface ChildTableProps {
  table: Table;
  /** Field names to show, in order. Defaults to the line model's visible fields. */
  columns?: string[];
  title?: string;
  icon?: string;
  tint?: Tint;
  /** Locks the lines (for example once the record is issued). */
  locked?: Prop<boolean>;
  lockedText?: string;
  /** Footer values by column; defaults to the parent's sums over this table. */
  totals?: Record<string, FieldLike | Prop<string>>;
  /** Totals under the table (net, VAT, grand total); defaults to the footer sums. */
  summary?: TotalItem[];
  prefs?: Cell<TablePrefs>;
  /** The record's name, shown as a crumb in focus view. */
  crumb?: Prop<string>;
  links?: LinkSource | null;
  /** Viewport height to assume before the container is measured. */
  height?: number;
}

type Spot = { key: string; col: string } | null;
const sameSpot = (a: Spot, b: Spot) => a === b || (!!a && !!b && a.key === b.key && a.col === b.col);

/** The parent's calculated sums over a table: { amount: order.total }. */
function parentSums(table: Table): Record<string, Field> {
  const sums: Record<string, Field> = {};
  for (const field of table.owner.fields.values()) {
    const compute = field.meta.compute as any;
    if (compute?.$expr === 'aggregate' && compute.table === table.name && compute.kind === 'sum' && compute.field) sums[compute.field] = field;
  }
  return sums;
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
  const columns = derive(() => arrange(allColumns, prefs.get(), true), { name: `${table.name} (shown columns)` });
  const widthOf = (column: ChildColumn) => prefs.get().widths[column.key] ?? column.width;
  const totalWidth = derive(() => CTL + columns.get().reduce((sum, column) => sum + widthOf(column), 0));
  const firstLeft = CTL;
  const locked = derive(() => Boolean(read(props.locked ?? false)), { name: `${table.name} (locked)` });
  const readOnly = derive(() => locked.get() || !record.ready.get());
  const sums = props.totals ?? parentSums(table);
  const summary = props.summary ?? Object.entries(sums).map(([key, value], index, all) => ({
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
    batch(() => { active.set(spot); editing.set(spot); });
    if (control) {
      control.focus();
      const input = control.element.matches('input') ? control.element as HTMLInputElement : control.element.querySelector('input');
      if (typed !== undefined && input) { input.value = typed; input.setSelectionRange?.(typed.length, typed.length); }
    }
    return true;
  };
  const stopEdit = (focus = true) => {
    const spot = editing.peek();
    editing.set(null);
    control = null;
    if (focus && spot) focusCell(spot);
  };
  const commit = (): boolean => !control || control.commit();
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

  const copyValues = (row: Row) => Object.fromEntries([...row.fields.values()].filter(field => !field.calculated).map(field => [field.name, untrack(() => field.value.get())]));
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
    const positions = list.map(row => ({ at: untrack(() => rows.get()).indexOf(row), values: copyValues(row) })).sort((a, b) => a.at - b.at);
    batch(() => {
      for (const row of list) table.remove(row);
      selection.set(new Set());
      if (list.some(row => row.key === expanded.peek())) expanded.set(null);
    });
    toast(list.length === 1 ? 'Deleted 1 row' : `Deleted ${list.length} rows`, {
      icon: 'trash-2',
      undo: () => batch(() => { for (const item of positions) { const row = table.add(item.values); table.move(row, Math.min(item.at, untrack(() => rows.get()).length - 1)); } }),
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

  const pinStyle = (column: () => ChildColumn) => () => columns.get()[0]?.key === column().key ? `${firstLeft}px` : null;
  const isFirst = (column: () => ChildColumn) => () => columns.get()[0]?.key === column().key;

  const header = (column: () => ChildColumn) => {
    const th = html`<th scope="col" role="columnheader" data-key=${column().key} class=${column().align === 'right' ? 'r' : ''} class:stk=${isFirst(column)} class:s2=${isFirst(column)}
      style:left=${pinStyle(column)}><span>${column().label}</span>${column().calculated ? Icon('lock', { size: 'xs', className: 'g-lk', label: 'Calculated' }) : null}</th>` as HTMLElement;
    th.append(resizeHandle(column().key, prefs, { current: () => th.offsetWidth || widthOf(column()), host: () => grid }));
    return th;
  };

  const cellView = (row: Row, column: () => ChildColumn, rowEdit: () => string | null, rowActive: () => string | null) => {
    const key = column().key, field = row.field(key), meta = field.meta, kind = kindOf(meta.type);
    const canEdit = () => !column().calculated && !readOnly.get() && field.editable.get();
    const display = RICH.has(meta.type) ? () => displayValue(field, { links: props.links, empty: '' }) : () => textOf(() => kind.format(field.value.get(), meta));
    const td = html`<td role="gridcell" data-col=${key} class=${`num${column().align === 'right' ? ' r' : ''}`} class:ed=${canEdit} class:calc=${() => !canEdit()}
      class:stk=${isFirst(column)} class:s2=${isFirst(column)} style:left=${pinStyle(column)} class:editing=${() => rowEdit() === key}
      class:invalid=${() => Boolean(field.error.get())} aria-invalid=${() => field.error.get() ? 'true' : null} aria-readonly=${() => canEdit() ? null : 'true'}
      tabindex=${() => rowActive() === key ? '0' : '-1'}>${when(() => rowEdit() === key, () => {
        control = FieldControl(field, {
          commitOn: 'manual', links: props.links, label: `${meta.label}, row ${indexOf(row.key) + 1}`,
          onCommit: !kind.control || kind.control === 'textarea' ? undefined : () => stopEdit(),
        });
        return control.element;
      }, display)}</td>` as HTMLElement;
    tooltip(td, () => field.error.get());
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
        ? html`<span class="muted g-x-hid">Hidden column</span>` : null}</div>`)}</div></div></td></tr>` as HTMLElement;
    queueMicrotask(() => expandedHeight.set(panel.getBoundingClientRect().height));
    return panel;
  };

  const rowView = (item: () => Row, index: () => number) => {
    const row = item(), number = () => view.range.get().start + index() + 1;
    const rowEdit = derive(() => editing.get()?.key === row.key ? editing.get()!.col : null);
    const rowActive = derive(() => {
      const spot = active.get();
      if (spot) return spot.key === row.key ? spot.col : null;
      return number() === 1 ? columns.get()[0]?.key ?? null : null;
    });
    const open = () => expanded.get() === row.key;
    const tr = html`<tr role="row" data-key=${row.key} class:sel=${() => selection.get().has(row.key)} class:open=${open}
      aria-rowindex=${() => String(number() + 1)} aria-selected=${() => String(selection.get().has(row.key))}>
      <td class="g-ctl stk" style="left:0" role="gridcell"><div class="g-ci">${Checkbox({ checked: () => selection.get().has(row.key), onChange: on => toggle(row.key, on), ariaLabel: () => `Select row ${number()}` })}
        <button type="button" class="g-exp" aria-expanded=${() => String(open())} aria-label=${() => `${open() ? 'Close' : 'Open'} row ${number()}`}
          on:click=${() => expanded.set(open() ? null : row.key)}><span class="num">${() => String(number())}</span>${Icon('chevron-right', { size: 'xs' })}</button></div></td>
      ${each(columns, column => column.key, column => cellView(row, column, () => rowEdit.get(), () => rowActive.get()))}</tr>`;
    return [tr, when(open, () => expandPanel(row))];
  };

  const columnsMenu = (anchor: HTMLElement) => openPopover(anchor, () => {
    const all = allColumns.map(column => column.key);
    const order = () => fullOrder(prefs.get(), all);
    const rowsOf = () => order().map((key, index) => {
      const column = allColumns.find(item => item.key === key)!, pinned = index === 0, hidden = () => prefs.get().hidden.includes(key);
      return html`<div class="col-row" class:hid=${hidden}>
        <button type="button" class="col-eye" disabled=${pinned} aria-label=${() => pinned ? `${column.label} is pinned` : `${hidden() ? 'Show' : 'Hide'} ${column.label}`}
          aria-pressed=${pinned ? null : () => String(!hidden())}
          on:click=${() => { const now = prefs.peek(); prefs.set({ ...now, hidden: hidden() ? now.hidden.filter(item => item !== key) : [...now.hidden, key] }); }}>${() => Icon(pinned ? 'lock' : hidden() ? 'eye-off' : 'eye', { size: 'sm' })}</button>
        <span class="lbl">${column.label}${pinned ? html`<span class="muted" style="font-size:11.5px"> · pinned</span>` : null}</span>
        ${IconButton({ icon: 'arrow-up', tip: `Move ${column.label} up`, size: 'small', disabled: pinned || index <= 1, onClick: () => moveColumn(prefs, all, key, index - 1) })}
        ${IconButton({ icon: 'arrow-down', tip: `Move ${column.label} down`, size: 'small', disabled: pinned || index === all.length - 1, onClick: () => moveColumn(prefs, all, key, index + 1) })}</div>`;
    });
    return html`<div class="mh">Columns · saved for you</div>${rowsOf}<hr><button type="button" class="mi" on:click=${() => prefs.set(emptyPrefs())}>${Icon('rotate-ccw', { size: 'sm' })}<span>Reset columns</span></button>`;
  }, { role: 'dialog', label: 'Columns', className: 'cols-pop' });

  const enterFocus = () => {
    const container = grid.closest('.view') ?? document.querySelector('.view');
    const box = container?.getBoundingClientRect();
    grid.style.top = `${box?.top ?? 0}px`;
    grid.style.left = `${box?.left ?? 0}px`;
    focused.set(true);
  };
  const leaveFocus = () => { focused.set(false); grid.style.top = ''; grid.style.left = ''; };

  const focusButton = () => {
    const button = html`<button type="button" class="btn sm" class:primary=${focused} aria-pressed=${() => String(focused.get())}
      on:click=${() => focused.peek() ? leaveFocus() : enterFocus()}>${() => Icon(focused.get() ? 'minimize-2' : 'maximize-2', { size: 'sm' })}<span class="hide-sm">${() => focused.get() ? 'Exit focus' : 'Focus'}</span></button>` as HTMLElement;
    tooltip(button, () => focused.get() ? 'Back to record (Esc)' : 'Focus view');
    return button;
  };
  const titleId = uid('ct');
  grid = html`<div class="grid" class:max=${focused} data-table=${table.name}>
    <div class="g-bar">
      ${when(focused, () => html`<span class="g-crumb">${props.crumb ?? ''}${Icon('chevron-right', { size: 'xs' })}</span>`)}
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
        <colgroup><col style:width=${`${CTL}px`}>${each(columns, column => column.key, column => html`<col style:width=${() => `${widthOf(column())}px`}>`)}</colgroup>
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
          class=${column().align === 'right' ? 'r num' : 'num'} class:stk=${isFirst(column)} class:s2=${isFirst(column)} style:left=${pinStyle(column)}>${() => sums[column().key]
            ? valueText(sums[column().key]) : index() === 0 ? `Total · ${rows.get().length} ${rows.get().length === 1 ? 'line' : 'lines'}` : ''}</td>`)}</tr></tfoot>`)}
      </table>
    </div>
    <div class="g-under">${when(locked, () => html`<span class="g-lock">${Icon('lock', { size: 'xs' })}${props.lockedText ?? 'Locked. Lines can’t change after the record is issued.'}</span>`,
      () => html`${Button({ icon: 'plus', label: 'Add row', size: 'small', className: 'g-add', disabled: () => !record.ready.get(), onClick: add })}<span class="muted hide-sm">${Key('Tab')} next cell · ${Key('Enter')} cell below · ${Key('Esc')} cancel</span>`)}
      <span class="grow"></span>${summary.length ? html`<div class="g-sum">${summary.map(item => html`<div class:gt=${Boolean(item.grand)}><span>${item.label}</span><b class="num">${item.value}</b></div>`)}</div>` : null}</div>
  </div>` as HTMLElement;
  scroller = grid.querySelector('.g-scroll') as HTMLElement;
  view.attach(scroller);
  const width = () => grid.style.setProperty('--gw', `${scroller.clientWidth || 600}px`);
  width();
  scroller.addEventListener('scroll', () => grid.classList.toggle('scrolled', scroller.scrollLeft > 0), { passive: true });

  // ---------- keyboard ----------

  grid.addEventListener('keydown', event => {
    const td = (event.target as Element).closest?.('td[data-col]') as HTMLElement | null;
    const tr = td?.closest('tr[data-key]') as HTMLElement | null;
    const spot: Spot = td && tr ? { key: tr.dataset.key!, col: td.dataset.col! } : null;
    const editingNow = editing.peek();
    if (editingNow) {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); return; }
      if (event.key === 'Tab') {
        event.preventDefault();
        if (!commit()) return;
        const next = step(editingNow, event.shiftKey ? -1 : 1);
        stopEdit(!next);
        if (next) startEdit(next);
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && !(event.target as Element).matches('textarea')) {
        event.preventDefault();
        if (!commit()) return;
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
    if (event.key === 'Enter' || event.key === 'F2') { event.preventDefault(); startEdit(spot); return; }
    if ((event.key === 'Delete' || event.key === 'Backspace') && editable(row, spot.col)) { event.preventDefault(); writeValue(row.field(spot.col), null); return; }
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey && event.key !== ' ') {
      const kind = kindOf(row.field(spot.col).meta.type);
      if (!kind.control || kind.control === 'textarea') { event.preventDefault(); startEdit(spot, event.key); }
    }
  });
  grid.addEventListener('click', event => {
    const td = (event.target as Element).closest?.('td[data-col]') as HTMLElement | null;
    const tr = td?.closest('tr[data-key]') as HTMLElement | null;
    if (!td || !tr || td.classList.contains('editing')) return;
    const spot = { key: tr.dataset.key!, col: td.dataset.col! };
    if (editing.peek()) { if (!commit()) return; stopEdit(false); }
    if (!startEdit(spot)) moveActive(spot);
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

