// DataTable: the list table. Sticky header, pinned first columns, columns that resize (double
// click resets) and reorder (drag a header, or Alt and an arrow key), sorting, row selection,
// keyboard navigation and virtualized rows, so only the rows in view exist in the DOM.
// Column widths and order live in a prefs cell the caller saves per user.

import { each, html, type Child } from '../dom';
import { cell, derive, effect, onCleanup, untrack, type Cell, type Derive } from '../reactive';
import { compare as compareNumbers, parse as parseNumber } from '../../runtime/FiniteEngine';
import { Checkbox, Icon } from './base';
import { read, type Prop } from './core';

// ---------- shared by DataTable and ChildTable ----------

/** Per-user column preferences: widths, order and hidden columns. */
export interface TablePrefs { widths: Record<string, number>; order: string[]; hidden: string[] }
export const emptyPrefs = (): TablePrefs => ({ widths: {}, order: [], hidden: [] });

/** Columns in the user's order (new columns keep their place at the end), without hidden ones. */
export function arrange<C extends { key: string }>(columns: C[], prefs: TablePrefs, keepFirst = false): C[] {
  const known = new Map(columns.map(column => [column.key, column]));
  const ordered = [...prefs.order.filter(key => known.has(key)), ...columns.map(column => column.key).filter(key => !prefs.order.includes(key))];
  if (keepFirst && columns.length) { ordered.splice(ordered.indexOf(columns[0].key), 1); ordered.unshift(columns[0].key); }
  return ordered.filter((key, index) => !prefs.hidden.includes(key) || (keepFirst && index === 0)).map(key => known.get(key)!);
}

/** A window of rows over a scroll container: what is in view plus a few either side. */
export function virtualWindow(options: { count: () => number; rowHeight: number; header: number; overscan?: number; fallback?: number;
  /** An expanded row that is taller than the others: its index and extra height. */
  extra?: () => { index: number; height: number } | null }) {
  const top = cell(0, { name: 'table.scrollTop' }), height = cell(options.fallback ?? 600, { name: 'table.viewport' });
  const overscan = options.overscan ?? 6, row = options.rowHeight;
  const range = derive(() => {
    const count = options.count(), extra = options.extra?.() ?? null;
    const y = Math.max(0, top.get() - options.header), bottom = y + height.get();
    const at = (position: number) => {
      if (!extra || position < (extra.index + 1) * row) return Math.floor(position / row);
      if (position < (extra.index + 1) * row + extra.height) return extra.index;
      return Math.floor((position - extra.height) / row);
    };
    const start = Math.max(0, Math.min(count, at(y) - overscan)), end = Math.max(start, Math.min(count, at(bottom) + 1 + overscan));
    const before = start * row + (extra && extra.index < start ? extra.height : 0);
    const total = count * row + (extra && extra.index < count ? extra.height : 0);
    const shown = (end - start) * row + (extra && extra.index >= start && extra.index < end ? extra.height : 0);
    return { start, end, before, after: Math.max(0, total - before - shown) };
  }, { name: 'table.range', equals: (a, b) => Boolean(a) && a.start === b.start && a.end === b.end && a.before === b.before && a.after === b.after });
  /** Attach to the scroll container. */
  const attach = (scroller: HTMLElement) => {
    const onScroll = () => top.set(scroller.scrollTop);
    scroller.addEventListener('scroll', onScroll, { passive: true });
    const measure = () => { if (scroller.clientHeight) height.set(scroller.clientHeight); };
    measure();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    observer?.observe(scroller);
    onCleanup(() => { scroller.removeEventListener('scroll', onScroll); observer?.disconnect(); });
  };
  /** Scroll so a row is in view. */
  const reveal = (scroller: HTMLElement, index: number) => {
    const extra = options.extra?.() ?? null;
    const y = options.header + index * row + (extra && extra.index < index ? extra.height : 0);
    const view = scroller.clientHeight || height.peek();
    if (y - options.header < scroller.scrollTop) scroller.scrollTop = y - options.header;
    else if (y + row > scroller.scrollTop + view) scroller.scrollTop = y + row - view;
    top.set(scroller.scrollTop);
  };
  return { top, height, range, attach, reveal };
}

/** Make a header's right edge resize its column. Double click resets. Widths go to prefs. */
export function resizeHandle(key: string, prefs: Cell<TablePrefs>, options: { min?: number; current: () => number; host: () => HTMLElement | null }): HTMLElement {
  const handle = html`<i class="rz" aria-hidden="true" title="Drag to resize, double-click to reset"></i>` as HTMLElement;
  const min = options.min ?? 60;
  handle.addEventListener('click', event => event.stopPropagation());
  handle.addEventListener('dblclick', event => {
    event.stopPropagation();
    const { [key]: _, ...widths } = prefs.peek().widths;
    prefs.set({ ...prefs.peek(), widths });
  });
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    event.stopPropagation();
    const start = event.clientX, width = options.current();
    handle.setPointerCapture?.(event.pointerId);
    options.host()?.classList.add('resizing');
    const move = (next: PointerEvent) => prefs.set({ ...prefs.peek(), widths: { ...prefs.peek().widths, [key]: Math.max(min, Math.round(width + next.clientX - start)) } });
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      options.host()?.classList.remove('resizing');
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
  return handle;
}

/** Every column key in the saved order, new columns last. */
export const fullOrder = (prefs: TablePrefs, all: string[]) => [...prefs.order.filter(item => all.includes(item)), ...all.filter(item => !prefs.order.includes(item))];

/** The element of a given tag whose data-key matches (keys are data, so no selector is built from them). */
export const byKey = (root: Element, tag: string, key: string | number) =>
  [...root.querySelectorAll<HTMLElement>(`${tag}[data-key]`)].find(element => element.dataset.key === String(key)) ?? null;

/** Move a column in the saved order. */
export function moveColumn(prefs: Cell<TablePrefs>, all: string[], key: string, to: number) {
  const order = fullOrder(prefs.peek(), all);
  const from = order.indexOf(key);
  if (from < 0 || to < 0 || to >= order.length || from === to) return;
  order.splice(from, 1);
  order.splice(to, 0, key);
  prefs.set({ ...prefs.peek(), order });
}

/** Compare two cell values for sorting: exact for numbers, natural for text, empty last. */
const isBlank = (value: unknown) => value === null || value === undefined || value === '';
export function compareValues(a: unknown, b: unknown): number {
  const blank = isBlank;
  if (blank(a) || blank(b)) return blank(a) === blank(b) ? 0 : blank(a) ? 1 : -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'boolean' || typeof b === 'boolean') return Number(a) - Number(b);
  const numeric = /^-?\d+(\.\d+)?$/;
  if (numeric.test(String(a)) && numeric.test(String(b))) return compareNumbers(parseNumber(String(a)), parseNumber(String(b)));
  return String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' });
}

// ---------- DataTable ----------

export interface Column<T> {
  key: string;
  label: string;
  width?: number;
  align?: 'left' | 'right';
  /** The value used for sorting and, without render, shown as text. */
  value?: (row: T) => unknown;
  /** Cell content; gets an accessor so a row that changes updates in place. */
  render?: (row: () => T) => Child;
  sortable?: boolean;
}

export type Sort = { key: string; dir: 'asc' | 'desc' } | null;

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: Prop<readonly T[]>;
  key: (row: T) => string | number;
  /** Accessible name of the table. */
  label: string;
  /** Data columns pinned while scrolling sideways (default 1). */
  pinned?: number;
  selectable?: boolean;
  selection?: Cell<Set<string | number>>;
  sort?: Cell<Sort>;
  /** Sort on the server: the table only reports the sort and shows rows as given. */
  serverSort?: boolean;
  onOpen?: (row: T) => void;
  prefs?: Cell<TablePrefs>;
  /** Footer values by column key; the first column shows totalsLabel. */
  totals?: Record<string, Prop<string>>;
  totalsLabel?: Prop<string>;
  empty?: () => Child;
  rowHeight?: number;
  /** Viewport height to assume before the container is measured. */
  height?: number;
}

const CHECK_WIDTH = 40;

/** List table with sticky header, pinned first column, resizable and reorderable columns. */
export function DataTable<T>(props: DataTableProps<T>): HTMLElement {
  const prefs = props.prefs ?? cell(emptyPrefs(), { name: `${props.label} (columns)` });
  const sort = props.sort ?? cell<Sort>(null);
  const selection = props.selection ?? cell(new Set<string | number>());
  const rowHeight = props.rowHeight ?? 44, pinned = props.pinned ?? 1;
  const columns = derive(() => arrange(props.columns, prefs.get()), { name: `${props.label} (shown columns)` });
  const widthOf = (column: Column<T>) => prefs.get().widths[column.key] ?? column.width ?? 160;
  const rows = derive(() => {
    const list = read(props.rows) ?? [];
    const order = sort.get();
    if (!order || props.serverSort) return list;
    const column = props.columns.find(item => item.key === order.key);
    if (!column) return list;
    const value = column.value ?? ((row: T) => (row as any)[column.key]);
    // Empty values stay last in both directions.
    const flip = order.dir === 'desc' ? -1 : 1;
    return [...list].sort((a, b) => {
      const x = value(a), y = value(b);
      if (isBlank(x) || isBlank(y)) return compareValues(x, y);
      return flip * compareValues(x, y);
    });
  }, { name: `${props.label} (rows)` });
  // Left offsets of pinned columns, by key.
  const pins: Derive<Map<string, { left: number; last: boolean }>> = derive(() => {
    const map = new Map<string, { left: number; last: boolean }>();
    let left = props.selectable ? CHECK_WIDTH : 0;
    columns.get().slice(0, pinned).forEach((column, index) => { map.set(column.key, { left, last: index === pinned - 1 }); left += widthOf(column); });
    return map;
  });
  const pinOf = (key: string) => derive(() => pins.get().get(key) ?? null, { equals: (a, b) => a === b || (Boolean(a) && Boolean(b) && a!.left === b!.left && a!.last === b!.last) });
  const pinCache = new Map<string, Derive<{ left: number; last: boolean } | null>>();
  const pin = (key: string) => { let item = pinCache.get(key); if (!item) pinCache.set(key, item = pinOf(key)); return item; };
  // One binding per cell for its pinned state: sticky class, last-pinned hairline and left offset.
  const bindPin = (element: HTMLElement, key: string) => effect(() => {
    const at = pin(key).get();
    element.classList.toggle('stk', Boolean(at));
    element.classList.toggle('stk-last', Boolean(at?.last));
    if (at) element.style.left = `${at.left}px`; else element.style.removeProperty('left');
  });
  const total = derive(() => (props.selectable ? CHECK_WIDTH : 0) + columns.get().reduce((sum, column) => sum + widthOf(column), 0));
  const active = cell<string | number | null>(null, { name: `${props.label} (active row)` });
  // The row that takes Tab focus: the active one, else the first.
  const focusKey = derive(() => active.get() ?? (rows.get()[0] ? props.key(rows.get()[0]) : null));
  const rowCount = derive(() => rows.get().length);
  const view = virtualWindow({ count: () => rows.get().length, rowHeight, header: 36, fallback: props.height });
  const visible = derive(() => { const { start, end } = view.range.get(); return rows.get().slice(start, end); });
  let scroller!: HTMLElement;

  const toggle = (key: string | number, on: boolean) => {
    const next = new Set(selection.peek());
    if (on) next.add(key); else next.delete(key);
    selection.set(next);
  };
  const allOn = () => rows.get().length > 0 && rows.get().every(row => selection.get().has(props.key(row)));
  const someOn = () => !allOn() && rows.get().some(row => selection.get().has(props.key(row)));

  const header = (column: () => Column<T>) => {
    const key = column().key, sortable = column().sortable !== false;
    const th = html`<th scope="col" class=${column().align === 'right' ? 'r' : ''} class:sortable=${sortable} class:stk=${() => Boolean(pin(key).get())}
      class:stk-last=${() => Boolean(pin(key).get()?.last)} style:left=${() => pin(key).get() ? `${pin(key).get()!.left}px` : null}
      draggable=${pinned > 0 && columns.get()[0]?.key === key ? 'false' : 'true'}
      aria-sort=${() => sort.get()?.key === key ? (sort.get()!.dir === 'asc' ? 'ascending' : 'descending') : sortable ? 'none' : null}
      tabindex="0" data-key=${key}><span>${column().label}</span>${() => sort.get()?.key === key
        ? html`<span class="si">${Icon(sort.get()!.dir === 'asc' ? 'arrow-up' : 'arrow-down', { size: 'xs' })}</span>` : null}</th>` as HTMLElement;
    th.append(resizeHandle(key, prefs, { current: () => th.offsetWidth || widthOf(column()), host: () => scroller }));
    const cycle = () => {
      if (!sortable) return;
      const now = sort.peek();
      sort.set(now?.key !== key ? { key, dir: 'asc' } : now.dir === 'asc' ? { key, dir: 'desc' } : null);
    };
    th.addEventListener('click', cycle);
    th.addEventListener('keydown', event => {
      const all = props.columns.map(item => item.key), shown = columns.peek().map(item => item.key);
      if ((event.key === 'Enter' || event.key === ' ') && !event.altKey) { event.preventDefault(); cycle(); }
      else if (event.altKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        event.preventDefault();
        const at = shown.indexOf(key), target = shown[at + (event.key === 'ArrowLeft' ? -1 : 1)];
        if (!target || (pinned > 0 && (at === 0 || shown.indexOf(target) === 0))) return;
        moveColumn(prefs, all, key, fullOrder(prefs.peek(), all).indexOf(target));
        queueMicrotask(() => byKey(scroller, 'th', key)?.focus());
      }
    });
    th.addEventListener('dragstart', event => { event.dataTransfer?.setData('text/x-evlet-column', key); });
    th.addEventListener('dragover', event => { if (event.dataTransfer?.types.includes('text/x-evlet-column')) event.preventDefault(); });
    th.addEventListener('drop', event => {
      const from = event.dataTransfer?.getData('text/x-evlet-column');
      if (!from || from === key) return;
      event.preventDefault();
      const all = props.columns.map(item => item.key), order = fullOrder(prefs.peek(), all);
      if (pinned > 0 && order.indexOf(key) === 0) return;
      moveColumn(prefs, all, from, order.indexOf(key));
    });
    return th;
  };

  const cellOf = (row: () => T, column: () => Column<T>) => {
    const key = column().key, content = column().render ? column().render!(row) : () => {
      const value = (column().value ?? ((item: T) => (item as any)[key]))(row());
      return value === null || value === undefined ? '' : String(value);
    };
    return html`<td role="gridcell" class=${column().align === 'right' ? 'r num' : ''} ref=${(td: HTMLElement) => bindPin(td, key)}>${content}</td>`;
  };

  const focusRow = (key: string | number) => queueMicrotask(() => {
    byKey(scroller, 'tr', key)?.focus();
  });
  const go = (index: number) => {
    const list = rows.peek();
    if (!list.length) return;
    const at = Math.max(0, Math.min(list.length - 1, index));
    const key = props.key(list[at]);
    active.set(key);
    view.reveal(scroller, at);
    focusRow(key);
  };

  const rowView = (row: () => T, index: () => number) => {
    const key = () => props.key(row());
    const selected = () => selection.get().has(key());
    return html`<tr role="row" data-key=${String(key())} class:sel=${selected} aria-selected=${props.selectable ? () => String(selected()) : null}
      aria-rowindex=${() => String(view.range.get().start + index() + 2)}
      tabindex=${() => focusKey.get() === key() ? '0' : '-1'}
      on:click=${(event: MouseEvent) => {
        if ((event.target as Element).closest('button,a,input,select,textarea,[role="checkbox"]')) return;
        active.set(key());
        props.onOpen?.(row());
      }}>${props.selectable ? html`<td role="gridcell" class="w-check stk" style="left:0">${Checkbox({ checked: selected, onChange: on => toggle(key(), on), ariaLabel: () => `Select row ${view.range.peek().start + index() + 1}` })}</td>` : null}${each(columns, column => column.key, column => cellOf(row, column))}</tr>`;
  };

  const colspan = () => String(columns.get().length + (props.selectable ? 1 : 0));
  const wrap = html`<div class="table-wrap" tabindex="-1">
    <table class="dt fixed" role="grid" aria-label=${props.label} aria-rowcount=${() => String(rowCount.get() + 1)} aria-multiselectable=${props.selectable ? 'true' : null}
      style:width=${() => `${total.get()}px`}>
      <colgroup>${props.selectable ? html`<col style:width=${`${CHECK_WIDTH}px`}>` : null}${each(columns, column => column.key, column => html`<col style:width=${() => `${widthOf(column())}px`}>`)}</colgroup>
      <thead><tr role="row" aria-rowindex="1">${props.selectable ? html`<th class="w-check stk" style="left:0" scope="col">${Checkbox({ checked: allOn, mixed: someOn, ariaLabel: 'Select all rows',
        onChange: on => selection.set(on ? new Set(rows.peek().map(props.key)) : new Set()) })}</th>` : null}${each(columns, column => column.key, header)}</tr></thead>
      <tbody>
        <tr class="spacer" aria-hidden="true"><td colspan=${colspan} style:height=${() => `${view.range.get().before}px`} style="padding:0;border:0"></td></tr>
        ${each(visible, props.key, rowView, { fallback: () => html`<tr><td colspan=${colspan} class="dt-empty">${props.empty ? props.empty() : html`<span class="muted">Nothing here yet.</span>`}</td></tr>` })}
        <tr class="spacer" aria-hidden="true"><td colspan=${colspan} style:height=${() => `${view.range.get().after}px`} style="padding:0;border:0"></td></tr>
      </tbody>
      ${props.totals ? html`<tfoot><tr role="row">${props.selectable ? html`<td class="w-check stk" style="left:0"></td>` : null}${each(columns, column => column.key, (column, index) => html`<td
        class=${column().align === 'right' ? 'r num' : ''} class:stk=${() => Boolean(pin(column().key).get())} style:left=${() => pin(column().key).get() ? `${pin(column().key).get()!.left}px` : null}>${() =>
          index() === 0 && !props.totals![column().key] ? read(props.totalsLabel ?? '') : props.totals![column().key] ? read(props.totals![column().key]) : ''}</td>`)}</tr></tfoot>` : null}
    </table></div>` as HTMLElement;
  scroller = wrap;
  view.attach(wrap);

  wrap.addEventListener('keydown', event => {
    const target = event.target as HTMLElement;
    if (target.tagName !== 'TR') return;
    const list = untrack(() => rows.get()), key = target.dataset.key;
    const at = list.findIndex(row => String(props.key(row)) === key);
    if (at < 0) return;
    const page = Math.max(1, Math.floor((wrap.clientHeight || view.height.peek()) / rowHeight) - 1);
    const moves: Record<string, number> = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: list.length - 1, PageDown: at + page, PageUp: at - page };
    if (event.key in moves) { event.preventDefault(); go(moves[event.key]); return; }
    if (event.key === 'Enter') { event.preventDefault(); props.onOpen?.(list[at]); }
    else if (event.key === ' ' && props.selectable) { event.preventDefault(); toggle(props.key(list[at]), !selection.peek().has(props.key(list[at]))); }
    else if (event.key === 'Escape' && props.selectable && selection.peek().size) { event.preventDefault(); selection.set(new Set()); }
  });
  // The pinned column casts a shadow once the table scrolls sideways.
  const sideways = () => wrap.classList.toggle('scrolled', wrap.scrollLeft > 0);
  wrap.addEventListener('scroll', sideways, { passive: true });
  // Keep the active row in the data when rows change.
  effect(() => { const list = rows.get(); const key = untrack(() => active.get()); if (key !== null && !list.some(row => props.key(row) === key)) active.set(null); });
  return wrap;
}

/** A column for a status pill, an avatar and so on, from a plain render function. */
export const column = <T>(key: string, label: string, options: Omit<Column<T>, 'key' | 'label'> = {}): Column<T> => ({ key, label, ...options });

