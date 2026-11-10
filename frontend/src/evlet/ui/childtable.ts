// ChildTable: the lines of a record as an editable grid bound to a record store Table.
//
// Pinned control column and first data column, sticky header and totals, resizable columns,
// a Columns menu (show, hide, reorder, reset), click or Enter to edit a cell, Tab and Shift Tab
// to the next editable cell (wrapping rows), Enter to the cell below, Esc to cancel, arrow keys
// between cells, Alt and an arrow to move a row. The table can take over the content area (focus
// view), and a row's Edit opens it there in the row editor: the rows as a narrow list on the left
// and the row's fields as one form on the right, in the same frame. Rows are virtualized: a 1,000-row table
// keeps only the rows in view in the DOM. It locks with the record.

import { each, html, when, type Child } from '../dom';
import { batch, cell, derive, effect, onCleanup, untrack, type Cell, type Readable } from '../reactive';
import type { Field, Row, Table, TableSection } from '../model/record';
import type { FieldSchema } from '../model/schema';
import { Button, Checkbox, Icon, IconButton, IconTile, Key } from './base';
import { read, textOf, uid, type Prop, type Tint } from './core';
import { BaseField, currencyFor, displayValue, FieldControl, fieldFor, writeValue, type Control, type FieldLike, type LinkSource } from './fields';
import { moneyChild } from './money';
import { kindOf } from './kinds';
import { openPopover, toast } from './layers';
import { dragList } from './draglist';
import { arrange, byKey, ColumnsMenu, emptyPrefs, pinnedKeys, pinTracker, resizeHandle, virtualWindow, type TablePrefs } from './table';
import { tooltip } from './tooltip';

const ROW = 38, HEADER = 36;
// Under 480 px the control column drops the row number and the first column starts narrower,
// so a phone keeps room for the other columns.
const NARROW = '(max-width: 480px)';
const narrowNow = () => { try { return Boolean(window.matchMedia?.(NARROW)?.matches); } catch { return false; } };
const WIDTHS: Record<string, number> = { text: 200, longtext: 260, link: 200, currency: 140, decimal: 110, integer: 90, percent: 100, date: 120, datetime: 160,
  time: 90, duration: 110, select: 140, boolean: 90, email: 200, url: 200, phone: 150, tags: 200, state: 120, sequence: 150, reference: 180 };
const RICH = new Set(['link', 'state', 'tags', 'sequence', 'boolean', 'currency']);
// The narrowest a column gets when the table shrinks its columns to fit.
const MIN_WIDTHS: Record<string, number> = { text: 120, longtext: 140, link: 120, currency: 100, decimal: 80, integer: 64, percent: 72, date: 104, datetime: 140,
  time: 72, duration: 80, select: 100, boolean: 64, email: 120, url: 120, phone: 110, tags: 120, state: 96, sequence: 110, reference: 120 };
// Columns a narrow table hides first: long text and lists, then calculated values, then the rest from the right.
const LOW = new Set(['longtext', 'tags', 'url', 'email']);

/** Columns that fit `room` px: shrink each toward its minimum first, then hide low-priority columns (never the
 * first, a pinned, a required or a summed one), and only when even that is too wide keep the minimums and let the
 * table scroll sideways. A column the person sized keeps its width. No room (0) leaves the natural widths. */
export function fitColumns<C extends { key: string; meta: FieldSchema; calculated: boolean }>(columns: C[], room: number,
  options: { width: (column: C) => number; sized: (column: C) => boolean; keep: (column: C) => boolean }): { shown: C[]; widths: Map<string, number>; hidden: C[] } {
  const natural = (column: C) => options.width(column);
  const least = (column: C) => options.sized(column) ? natural(column) : Math.min(natural(column), MIN_WIDTHS[column.meta.type] ?? 100);
  const total = (list: C[], size: (column: C) => number) => list.reduce((sum, column) => sum + size(column), 0);
  const widths = (list: C[], size: (column: C) => number) => new Map(list.map(column => [column.key, size(column)]));
  if (room <= 0 || total(columns, natural) <= room) return { shown: columns, widths: widths(columns, natural), hidden: [] };
  const shown = [...columns], hidden: C[] = [];
  const rank = (column: C) => LOW.has(column.meta.type) ? 0 : column.calculated ? 1 : 2;
  while (total(shown, least) > room) {
    const candidates = shown.filter((column, index) => index > 0 && !options.keep(column));
    if (!candidates.length) return { shown, widths: widths(shown, least), hidden: columns.filter(column => hidden.includes(column)) };
    const drop = candidates.reduce((worst, column) => rank(column) < rank(worst) || (rank(column) === rank(worst) && shown.indexOf(column) > shown.indexOf(worst)) ? column : worst);
    shown.splice(shown.indexOf(drop), 1);
    hidden.push(drop);
  }
  // Share the room left over the minimums by how much each column gave up, never past its natural width.
  const spare = room - total(shown, least), give = total(shown, column => natural(column) - least(column));
  const share = give > 0 ? Math.min(1, spare / give) : 0;
  return { shown, widths: widths(shown, column => Math.floor(least(column) + (natural(column) - least(column)) * share)), hidden: columns.filter(column => hidden.includes(column)) };
}

export interface ChildColumn { key: string; label: string; width: number; meta: FieldSchema; calculated: boolean; align: 'left' | 'right' }
export interface TotalItem { label: string; value: Prop<Child>; grand?: boolean }

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
  /** Rows nobody edits here (a table the record fills itself): no selection, drag handles or Edit, only the row
   * number, and no lock line unless lockedText says something. */
  derived?: boolean;
  /** Footer values by column; defaults to the parent's sums over this table. */
  totals?: Record<string, FieldLike | Prop<Child>>;
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
  /** The row editor's form; without it, every field in a grid. `field` gives a field of the row as the table
   * shows it (read only while the lines are locked). */
  rowForm?: (row: Row, field: (name: string) => FieldLike) => Child;
  /** Hands the caller the table's Focus and Columns, for buttons of its own (the record page's table title). */
  controls?: (controls: ChildTableControls) => void;
}

/** What a caller can do with a ChildTable from outside it. */
export interface ChildTableControls {
  focused: Readable<boolean>;
  toggleFocus(): void;
  columns(anchor: HTMLElement): void;
  /** Whether the table takes section rows, and adding one at the end (it starts being renamed). */
  sectioned: boolean;
  addSection(): void;
}

/** A row's key values on one line: "4 × 2,228.26 = 8,913.04" from two values typed in and a calculated one, else
 * the first values that are set. */
export function rowSummary(row: Row, columns: ChildColumn[]): string {
  // A Decimal shows its significant digits ("2", not "2.0000"); other values as the field formats them.
  const text = (column: ChildColumn) => {
    const shown = kindOf(column.meta.type).format(untrack(() => row.field(column.key).value.get()), column.meta);
    return column.meta.type === 'decimal' ? shown.replace(/(\.\d*?)0+(?=\D*$)/, '$1').replace(/\.(?=\D*$)/, '') : shown;
  };
  const numeric = columns.slice(1).filter(column => NUMERIC.has(column.meta.type));
  const typed = numeric.filter(column => !column.calculated), derived = numeric.filter(column => column.calculated);
  if (typed.length >= 2 && derived.length && typed.slice(0, 2).every(column => text(column))) {
    const result = derived[derived.length - 1];
    return `${text(typed[0])} × ${text(typed[1])} = ${text(result) || '…'}`;
  }
  return columns.slice(1).map(text).filter(Boolean).slice(0, 3).join(' · ');
}
const NUMERIC = new Set(['integer', 'decimal', 'currency', 'percent']);

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

// A total's value: money with the site's currency symbol, anything else as text.
const valueText = (value: FieldLike | Prop<Child>): Child => {
  if (value && typeof value === 'object' && 'meta' in value) {
    const meta = value.meta;
    if (meta.type === 'currency') return moneyChild(value.value.get(), currencyFor(value)(), typeof meta.scale === 'number' ? meta.scale : 2);
    return kindOf(meta.type).format(value.value.get(), meta);
  }
  return read(value as Prop<Child>) ?? '';
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
  const arranged = derive(() => arrange(allColumns, prefs.get()), { name: `${table.name} (arranged columns)` });
  const narrow = cell(narrowNow(), { name: `${table.name} (narrow)` });
  // The control column: checkbox, drag handle and the row's number with Edit (a phone keeps a narrow handle, without the number).
  const derived = Boolean(props.derived);
  const ctl = () => derived ? 44 : narrow.get() ? 72 : 104;
  const baseWidth = (column: ChildColumn) => prefs.get().widths[column.key]
    ?? (narrow.get() && arranged.get()[0]?.key === column.key ? Math.min(column.width, 120) : column.width);
  const allKeys = allColumns.map(column => column.key);
  // The table's visible width; columns fit it before the table scrolls sideways (fitColumns). 0 until measured.
  const room = cell(0, { name: `${table.name} (room)` });
  const sums = props.totals ?? parentSums(table), sumKeys = new Set(Object.keys(sums));
  const fit = derive(() => {
    const pinned = pinnedKeys(prefs.get(), allKeys, 1);
    return fitColumns(arranged.get(), room.get() - ctl(), { width: baseWidth, sized: column => prefs.get().widths[column.key] !== undefined,
      keep: column => pinned.has(column.key) || sumKeys.has(column.key) || Boolean(column.meta.required) });
  }, { name: `${table.name} (fitted columns)` });
  const columns = derive(() => fit.get().shown, { name: `${table.name} (shown columns)` });
  const hiddenToFit = derive(() => fit.get().hidden, { name: `${table.name} (hidden to fit)` });
  const widthOf = (column: ChildColumn) => fit.get().widths.get(column.key) ?? baseWidth(column);
  const totalWidth = derive(() => ctl() + columns.get().reduce((sum, column) => sum + widthOf(column), 0));
  // Pinned columns stay on the left after the row control column (one by default).
  const { pin } = pinTracker(() => columns.get(), () => pinnedKeys(prefs.get(), allKeys, 1), widthOf, ctl);
  const locked = derive(() => Boolean(read(props.locked ?? false)), { name: `${table.name} (locked)` });
  const readOnly = derive(() => locked.get() || !record.ready.get());
  const grand = props.totals ? [] : parentGrandTotals(table, sums as Record<string, Field>);
  const summary: Array<TotalItem & { column?: string }> = props.summary ?? [...Object.entries(sums), ...grand.map(field => [field.meta.name, field] as const)].map(([key, value], index, all) => ({
    label: value && typeof value === 'object' && 'meta' in value ? value.meta.label : allColumns.find(column => column.key === key)?.label ?? key,
    value: () => valueText(value), grand: index === all.length - 1 && all.length > 1, column: index < Object.keys(sums).length ? key : undefined,
  }));
  // A column's sum shows once: in the total line while that column shows, under the table only when it doesn't.
  const summaryShown = derive(() => summary.filter(item => !item.column || rows.get().length === 0 || !columns.get().some(column => column.key === item.column)));

  const rows = table.rows;
  const selection = cell(new Set<string>(), { name: `${table.name} (selection)` });
  const editing = cell<Spot>(null, { name: `${table.name} (editing)`, equals: sameSpot });
  const active = cell<Spot>(null, { name: `${table.name} (active cell)`, equals: sameSpot });
  // The row open in the row editor, and whether opening it took the table into focus view.
  const expanded = cell<string | null>(null, { name: `${table.name} (row editor)` });
  let focusedForEditor = false;
  const focused = cell(false, { name: `${table.name} (focus view)` });
  const indexOf = (key: string) => untrack(() => rows.get()).findIndex(row => row.key === key);
  const view = virtualWindow({
    count: () => rows.get().length, rowHeight: ROW, header: HEADER, fallback: props.height ?? 560,
  });
  const visible = derive(() => { const { start, end } = view.range.get(); return rows.get().slice(start, end); }, { name: `${table.name} (visible rows)` });

  // ---------- section rows ----------
  // A table whose schema has sections ("sections": true) groups its lines under section rows. With any section, every
  // row is drawn (a sectioned table is a document's lines, not thousands of rows), and a closed section hides its lines.
  const sectioned = Boolean(table.meta.sections);
  const hasSections = derive(() => sectioned && table.sections.get().length > 0, { name: `${table.name} (has sections)` });
  const closedSections = cell(new Set<string>(), { name: `${table.name} (closed sections)` });
  const renaming = cell<string | null>(null, { name: `${table.name} (renaming section)` });
  /** The lines under each section, by section key, in order. */
  const sectionLines = derive(() => {
    const out = new Map<string, Row[]>(), sections = table.sections.get();
    let current: string | null = null;
    for (const row of rows.get()) {
      for (const section of sections) if (section.before === row.key) { current = section.key; out.set(current, []); }
      if (current) out.get(current)!.push(row);
    }
    for (const section of sections) if (!out.has(section.key)) out.set(section.key, []);
    return out;
  }, { name: `${table.name} (section lines)` });
  type Entry = { kind: 'row'; row: Row } | { kind: 'section'; section: TableSection };
  const entries = derive<Entry[]>(() => {
    if (!hasSections.get()) return visible.get().map(row => ({ kind: 'row' as const, row }));
    const out: Entry[] = [], sections = table.sections.get(), closed = closedSections.get(), list = rows.get();
    let current: string | null = null;
    for (const row of list) {
      for (const section of sections) if (section.before === row.key) { out.push({ kind: 'section', section }); current = section.key; }
      if (!current || !closed.has(current)) out.push({ kind: 'row', row });
    }
    for (const section of sections) if (section.before === null || !list.some(row => row.key === section.before)) out.push({ kind: 'section', section });
    return out;
  }, { name: `${table.name} (entries)` });
  /** The line fields a section row sums (the schema's section_sums); none without it. */
  const sectionSums = new Set(table.meta.section_sums ?? []);
  /** The sum of a column over some lines, exact (decimal text), formatted as the total line formats it. */
  const sumOf = (lines: Row[], column: ChildColumn): Child => {
    let scale = 0;
    const values = lines.map(row => String(row.field(column.key).value.get() ?? '').trim()).filter(value => /^-?\d+(\.\d+)?$/.test(value));
    for (const value of values) scale = Math.max(scale, (value.split('.')[1] ?? '').length);
    const total = values.reduce((sum, value) => { const [whole, part = ''] = value.replace('-', '').split('.'); const units = BigInt(whole + part.padEnd(scale, '0')); return sum + (value.startsWith('-') ? -units : units); }, 0n);
    const negative = total < 0n, digits = (negative ? -total : total).toString().padStart(scale + 1, '0');
    const text = `${negative ? '-' : ''}${scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits}`;
    const parent = sums[column.key], places = typeof column.meta.scale === 'number' ? column.meta.scale : 2;
    if (column.meta.type === 'currency') {
      const source = parent && typeof parent === 'object' && 'meta' in parent ? parent : lines[0] ? lines[0].field(column.key) : null;
      if (source) return moneyChild(text, currencyFor(source)(), places);
    }
    return kindOf(column.meta.type).format(text, column.meta);
  };
  const addSection = () => {
    if (readOnly.peek()) return;
    const section = table.addSection('New section', null);
    renaming.set(section.key);
  };
  const toggleSection = (key: string) => {
    const next = new Set(closedSections.peek());
    if (next.has(key)) next.delete(key); else next.add(key);
    closedSections.set(next);
  };
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
  // open: a click, Enter or F2 also opens the cell's picker (a Link search, a list, a date); typing does not.
  const startEdit = (spot: NonNullable<Spot>, typed?: string, open = false) => {
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
      else if (open && control.open) { const opening = control; setTimeout(() => { if (control === opening && opening.element.isConnected) opening.open!(); }); }
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

  // A row opened in the row editor follows the table: a locked or loading table shows its values
  // read only, as the cells do. A view over the field, so writes and recordOf() still reach it.
  const rowField = (field: Field): FieldLike => Object.create(field, {
    editable: { value: derive(() => !readOnly.get() && field.editable.get()) },
    // Locked lines say so once, under the table, not beside each field of the row editor.
    recordLocked: { value: locked },
    rules: { value: derive(() => { const rules = field.rules.get(); return locked.get() && !rules.locked ? { ...rules, locked: true } : rules; }) },
    set: { value: (value: unknown) => field.set(value) },
    touched: { get: () => field.touched, set: (value: boolean) => { field.touched = value; } },
  }) as FieldLike;

  // ---------- the row editor ----------

  const rowTitle = (row: Row) => { const first = columns.get()[0]; return first ? kindOf(first.meta.type).format(row.field(first.key).value.get(), first.meta) || 'New row' : 'New row'; };
  const openEditor = (key: string) => {
    if (!focused.peek()) { focusedForEditor = true; enterFocus(); }
    expanded.set(key);
  };
  const closeEditor = (leave = focusedForEditor) => {
    const key = expanded.peek();
    expanded.set(null);
    if (leave) { focusedForEditor = false; leaveFocus(); }
    if (key) queueMicrotask(() => { reveal(key); byKey(scroller, 'tr', key)?.querySelector<HTMLElement>('.g-exp')?.focus(); });
  };
  const stepEditor = (direction: 1 | -1) => {
    const list = rows.peek(), at = list.findIndex(row => row.key === expanded.peek()) + direction;
    if (at >= 0 && at < list.length) expanded.set(list[at].key);
  };
  const insertBelow = (row: Row) => {
    if (readOnly.peek()) return;
    const added = table.add();
    table.move(added, untrack(() => rows.get()).indexOf(row) + 1);
    expanded.set(added.key);
  };
  const deleteLine = (row: Row) => {
    const list = rows.peek(), at = list.indexOf(row);
    const next = list[at + 1] ?? list[at - 1] ?? null;
    remove([row]);
    if (next) expanded.set(next.key); else closeEditor();
  };
  // The rows as a narrow list (checkbox, number, title) and the row's fields as one form beside it.
  const editorView = () => {
    const list = html`<div class="g-ed-list" role="listbox" aria-label=${`${props.title ?? table.meta.label}: rows`}>${each(rows, item => item.key, (item, at) => html`<div role="option" class="g-ed-item"
      data-key=${item().key} aria-selected=${() => String(expanded.get() === item().key)} tabindex=${() => expanded.get() === item().key ? '0' : '-1'}
      on:click=${(event: MouseEvent) => { if (!(event.target as Element).closest('.check')) expanded.set(item().key); }}>
      ${Checkbox({ checked: () => selection.get().has(item().key), onChange: on => toggle(item().key, on), ariaLabel: () => `Select row ${at() + 1}` })}
      <span class="num g-ed-no">${() => String(at() + 1)}</span><span class="g-ed-t">${() => rowTitle(item())}</span></div>`)}</div>` as HTMLElement;
    list.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      stepEditor(event.key === 'ArrowDown' ? 1 : -1);
      queueMicrotask(() => list.querySelector<HTMLElement>('[aria-selected="true"]')?.focus());
    });
    // The form follows the row the editor is on; the list stays, with its scroll position.
    const main = () => {
      const key = expanded.get();
      const row = key ? untrack(() => rows.get()).find(item => item.key === key) : undefined;
      return row ? untrack(() => editorMain(row)) : null;
    };
    return html`<div class="g-ed" role="region" aria-label=${() => `${props.title ?? table.meta.label}: row editor`}>${list}${main}</div>` as HTMLElement;
  };
  const editorMain = (row: Row) => {
    const index = () => rows.get().indexOf(row);
    const field = (name: string) => rowField(row.field(name));
    const form = props.rowForm ? props.rowForm(row, field)
      : html`<div class="g-x-f">${allColumns.map(column => html`<div class=${column.meta.type === 'longtext' ? 'wide' : ''}>${BaseField({ field: field(column.key), links: props.links })}</div>`)}</div>`;
    return html`<div class="g-ed-main" role="group" aria-label=${() => `Row ${index() + 1}: ${rowTitle(row)}`}>
        <div class="g-ed-h"><span class="g-ed-badge num">${() => String(index() + 1)}</span>
          <div class="g-ed-ttl"><b>${() => rowTitle(row)}</b><span class="g-ed-sum">${() => { rows.get(); return rowSummary(row, columns.get()); }}</span></div>
          <span class="g-ed-pos num">${() => `${index() + 1} of ${rows.get().length}`}</span>
          ${IconButton({ icon: 'chevron-up', tip: 'Previous row', size: 'small', disabled: () => index() <= 0, onClick: () => stepEditor(-1) })}
          ${IconButton({ icon: 'chevron-down', tip: 'Next row', size: 'small', disabled: () => index() >= rows.get().length - 1, onClick: () => stepEditor(1) })}
          ${IconButton({ icon: 'x', tip: 'Close (Esc)', size: 'small', onClick: () => closeEditor() })}</div>
        <div class="g-ed-body">${form}
          ${[...row.tables.values()].map(nested => html`<div class="g-x-nested">${ChildTable({ table: nested, locked: props.locked, links: props.links, height: 228,
            lockedText: props.lockedText, icon: 'rows-3', tint: props.tint })}</div>`)}</div>
        <div class="g-ed-f">${when(() => !readOnly.get(), () => [
          Button({ icon: 'trash-2', label: 'Delete line', variant: 'ghost', size: 'small', className: 'g-ed-del', onClick: () => deleteLine(row) }),
          Button({ icon: 'plus', label: 'Insert below', variant: 'ghost', size: 'small', onClick: () => insertBelow(row) })])}
          <span class="grow"></span>${Button({ label: 'Done', variant: 'primary', size: 'small', onClick: () => closeEditor() })}</div>
      </div>` as HTMLElement;
  };

  const rowView = (item: () => Row, index: () => number) => {
    const row = item(), number = () => hasSections.get() ? rows.get().indexOf(row) + 1 : view.range.get().start + index() + 1;
    const rowEdit = derive(() => editing.get()?.key === row.key ? editing.get()!.col : null);
    const rowSpot = derive(() => { const spot = active.get(); return spot?.key === row.key ? spot.col : null; });
    // The one cell Tab reaches from outside the grid (roving tabindex): the active cell, or the first.
    const rowActive = derive(() => active.get() ? rowSpot.get() : number() === 1 ? columns.get()[0]?.key ?? null : null);
    const tr = html`<tr role="row" data-key=${row.key} class:sel=${() => selection.get().has(row.key)} data-drag-item="line" data-drag-id=${row.key}
      aria-rowindex=${() => String(number() + 1)} aria-selected=${() => String(selection.get().has(row.key))}>
      <td class="g-ctl stk" style="left:0" role="gridcell">${derived ? html`<div class="g-ci g-ci-n"><span class="num muted">${() => String(number())}</span></div>` : html`<div class="g-ci">${Checkbox({ checked: () => selection.get().has(row.key), onChange: on => toggle(row.key, on), ariaLabel: () => `Select row ${number()}` })}
        <button type="button" class="g-drag" data-drag-handle="line" aria-label=${() => `Move row ${number()}`} aria-roledescription="draggable row" disabled=${() => readOnly.get()}>${Icon('grip-vertical', { size: 'xs' })}</button>
        <button type="button" class="g-exp" aria-label=${() => `Edit row ${number()}`} title="Edit"
          on:click=${() => openEditor(row.key)}><span class="num">${() => String(number())}</span>${Icon('pencil', { size: 'xs' })}</button></div>`}</td>
      ${each(columns, column => column.key, column => cellView(row, column, () => rowEdit.get(), () => rowActive.get(), () => rowSpot.get()))}</tr>`;
    return tr;
  };

  // A section row: drag handle, a chevron that hides its lines, the label (renamed in place), "N lines", Delete, and the
  // sums of its own lines in the columns the total line sums.
  const sectionView = (item: () => TableSection) => {
    const key = item().key;
    const lines = () => sectionLines.get().get(key) ?? [];
    const open = () => !closedSections.get().has(key);
    const label = () => {
      // A locked or confirmed record refuses section edits: the label is plain text then.
      if (readOnly.get()) return html`<b class="g-sec-name">${() => item().label}</b>`;
      if (renaming.get() !== key) return html`<button type="button" class="g-sec-name" title="Rename" on:click=${() => renaming.set(key)}><b>${() => item().label}</b></button>`;
      const input = html`<input class="input g-sec-input" aria-label="Section name" value=${untrack(() => item().label)}>` as HTMLInputElement;
      const done = (keep: boolean) => { if (renaming.peek() !== key) return; const text = input.value.trim(); renaming.set(null); if (keep && text && text !== untrack(() => item().label)) table.renameSection(key, text); };
      input.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); done(true); }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); done(false); }
      });
      input.addEventListener('blur', () => done(true));
      queueMicrotask(() => { input.focus(); input.select(); });
      return input;
    };
    return html`<tr class="g-sec" role="row" data-key=${`s:${key}`} data-drag-item="line" data-drag-id=${`s:${key}`}>
      <td class="g-ctl stk" style="left:0" role="gridcell"><div class="g-ci">
        ${when(() => !readOnly.get() && !derived, () => html`<button type="button" class="g-drag" data-drag-handle="line" aria-label=${() => `Move section ${item().label}`} aria-roledescription="draggable section">${Icon('grip-vertical', { size: 'xs' })}</button>`)}
        <button type="button" class="g-sec-tog" aria-expanded=${() => String(open())} aria-label=${() => `${open() ? 'Hide' : 'Show'} the lines of ${item().label}`}
          on:click=${() => toggleSection(key)}>${Icon('chevron-down', { size: 'xs' })}</button></div></td>
      ${each(columns, column => column.key, (column, index) => html`<td role="gridcell" class=${column().align === 'right' ? 'r num' : ''} class:stk=${isPinned(column)} class:s2=${isLastPin(column)} style:left=${pinStyle(column)}>${() => index() === 0
        ? html`<span class="g-sec-l">${label}<span class="muted g-sec-n">${() => `${lines().length} ${lines().length === 1 ? 'line' : 'lines'}`}</span>${when(() => !readOnly.get(), () => IconButton({ icon: 'trash-2', tip: () => `Delete section ${item().label} (its lines stay)`, size: 'small', onClick: () => table.removeSection(key) }))}</span>`
        : sectionSums.has(column().key) ? sumOf(lines(), column()) : ''}</td>`)}</tr>`;
  };
  /** A line or section moved by its drag handle to before item `before` (a line key, s:<section key>, or null for the end). */
  const moveLine = (id: string, before: string | null) => {
    if (readOnly.peek()) return;
    const list = rows.peek(), sections = table.sections.peek();
    const anchorOf = (target: string | null): string | null => target === null ? null : target.startsWith('s:') ? sections.find(section => section.key === target.slice(2))?.before ?? null : target;
    if (id.startsWith('s:')) { table.moveSection(id.slice(2), anchorOf(before), before?.startsWith('s:') ? before.slice(2) : undefined); return; }
    const row = list.find(item => item.key === id);
    if (!row || before === id) return;
    batch(() => {
      // Sections that started at this line start at the next one; dropped on a section's first line, the line joins it.
      const next = list[list.indexOf(row) + 1]?.key ?? null;
      for (const section of sections) if (section.before === row.key) table.moveSection(section.key, next);
      const target = anchorOf(before), rest = list.filter(item => item !== row);
      table.move(row, target === null ? rest.length : Math.max(0, rest.findIndex(item => item.key === target)));
      if (before && !before.startsWith('s:')) for (const section of table.sections.peek()) if (section.before === before) table.moveSection(section.key, row.key);
    });
  };

  const columnsMenu = (anchor: HTMLElement) => openPopover(anchor, () => ColumnsMenu({ columns: allColumns, prefs, pinned: 1,
    widthOf: key => baseWidth(allColumns.find(column => column.key === key)!), viewWidth: () => scroller.clientWidth - ctl() }),
  { role: 'dialog', label: 'Columns', className: 'cols-pop' });

  const enterFocus = () => {
    const container = grid.closest('.view') ?? document.querySelector('.view');
    const box = container?.getBoundingClientRect();
    grid.style.top = `${box?.top ?? 0}px`;
    grid.style.left = `${box?.left ?? 0}px`;
    focused.set(true);
  };
  const leaveFocus = () => { expanded.set(null); focusedForEditor = false; focused.set(false); grid.style.top = ''; grid.style.left = ''; };

  // Focus view keeps the record's header above the table (it does not scroll with the rows).
  const focusHead = (heading: FocusHeading) => html`<header class="g-rec" aria-label=${() => `${heading.type} ${read(heading.title) ?? ''}`}>
    ${heading.mark ? html`<span class="g-rec-mark">${heading.mark()}</span>` : null}
    <span class="g-rec-type">${heading.icon ? Icon(heading.icon, { size: 'xs' }) : null}${heading.type}</span>
    <b class="g-rec-title">${heading.title}</b>${heading.number ? html`<span class="g-rec-no mono">${heading.number}</span>` : null}
    <span class="grow"></span>${Button({ icon: 'arrow-left', label: 'Back to form', variant: 'ghost', size: 'small', hideLabelOnPhone: true, onClick: () => { leaveFocus(); grid.querySelector<HTMLElement>('.g-bar [aria-pressed]')?.focus(); } })}</header>`;

  props.controls?.({ focused, toggleFocus: () => focused.peek() ? leaveFocus() : enterFocus(), columns: anchor => columnsMenu(anchor), sectioned, addSection });
  const focusButton = () => {
    const button = html`<button type="button" class="btn sm" class:primary=${focused} aria-pressed=${() => String(focused.get())}
      on:click=${() => focused.peek() ? leaveFocus() : enterFocus()}>${() => Icon(focused.get() ? 'minimize-2' : 'maximize-2', { size: 'sm' })}<span class="hide-sm">${() => focused.get() ? 'Exit focus' : 'Focus'}</span></button>` as HTMLElement;
    tooltip(button, () => focused.get() ? 'Back to record (Esc)' : 'Focus view');
    return button;
  };
  const titleId = uid('ct');
  grid = html`<div class="grid" class:max=${focused} class:g-editing=${() => expanded.get() !== null} data-table=${table.name}>
    ${props.heading ? when(focused, () => focusHead(props.heading!)) : null}
    <div class="g-bar">
      ${props.heading ? null : when(focused, () => html`<span class="g-crumb">${props.crumb ?? ''}${Icon('chevron-right', { size: 'xs' })}</span>`)}
      <div class="g-t" id=${titleId}>${IconTile(props.icon ?? 'table-2', { tint: props.tint ?? 'blue', size: 26 })}${props.title ?? table.meta.label}<span class="n num" aria-label=${() => `${rows.get().length} rows`}>${() => String(rows.get().length)}</span></div>
      ${when(() => selection.get().size > 0 && !readOnly.get(), () => html`<span class="g-selinfo" role="status">${() => `${selection.get().size} selected`}</span>
        ${Button({ icon: 'copy', label: 'Duplicate', variant: 'ghost', size: 'small', onClick: () => duplicate(selectedRows()) })}
        ${Button({ icon: 'trash-2', label: 'Delete', variant: 'soft-danger', size: 'small', onClick: () => remove(selectedRows()) })}`)}
      <span class="grow"></span>
      ${when(() => hiddenToFit.get().length > 0 && rows.get().length > 0, () => {
        const more = html`<button type="button" class="g-more link-q" on:click=${() => openEditor((rows.peek()[0]).key)}>${() => {
          const count = hiddenToFit.get().length; return `${count} more ${count === 1 ? 'column' : 'columns'}`; }}</button>` as HTMLElement;
        tooltip(more, () => `Hidden to fit: ${hiddenToFit.get().map(column => column.label).join(', ')}. Open a row to see every field.`);
        return more;
      })}
      ${(() => { const button = Button({ icon: 'columns-3', label: 'Columns', size: 'small', hideLabelOnPhone: true, onClick: () => columnsMenu(button) }); button.setAttribute('aria-haspopup', 'dialog'); return button; })()}
      ${focusButton()}
    </div>
    ${when(() => expanded.get() !== null, editorView)}
    <div class="g-scroll" tabindex="-1">
      <table class="g" role="grid" aria-labelledby=${titleId} aria-rowcount=${() => String(rows.get().length + 1)} aria-colcount=${() => String(columns.get().length + 1)}
        aria-readonly=${() => readOnly.get() ? 'true' : null} style:width=${() => `${totalWidth.get()}px`}>
        <colgroup><col style:width=${() => `${ctl()}px`}>${each(columns, column => column.key, column => html`<col style:width=${() => `${widthOf(column())}px`}>`)}</colgroup>
        <thead><tr role="row" aria-rowindex="1"><th class="g-ctl stk" style="left:0" scope="col">${derived ? html`<span class="sr-only">Row</span>` : Checkbox({ checked: () => rows.get().length > 0 && selectedRows().length === rows.get().length,
          mixed: () => selectedRows().length > 0 && selectedRows().length < rows.get().length, ariaLabel: 'Select all rows',
          onChange: on => selection.set(on ? new Set(rows.peek().map(row => row.key)) : new Set()) })}</th>${each(columns, column => column.key, header)}</tr></thead>
        <tbody data-drop-zone="line" data-zone="lines">
          <tr class="spacer" aria-hidden="true"><td colspan=${() => String(columns.get().length + 1)} style="padding:0;border:0" style:height=${() => `${hasSections.get() ? 0 : view.range.get().before}px`}></td></tr>
          ${each(entries, entry => entry.kind === 'row' ? entry.row.key : `s:${entry.section.key}`, (entry, index) => entry().kind === 'row'
            ? rowView(() => (entry() as { row: Row }).row, index) : sectionView(() => (entry() as { section: TableSection }).section), { fallback: () => html`<tr><td colspan=${() => String(columns.get().length + 1)} class="g-empty"><div class="g-empty-in">${Icon(props.icon ?? 'table-2', { size: 'lg', className: 'muted' })}<span>No rows yet</span>${when(() => !readOnly.get(),
            () => Button({ icon: 'plus', label: 'Add the first row', variant: 'primary', size: 'small', onClick: add }))}</div></td></tr>` })}
          <tr class="spacer" aria-hidden="true"><td colspan=${() => String(columns.get().length + 1)} style="padding:0;border:0" style:height=${() => `${hasSections.get() ? 0 : view.range.get().after}px`}></td></tr>
        </tbody>
        ${when(() => rows.get().length > 0 && Object.keys(sums).length > 0, () => html`<tfoot><tr role="row"><td class="g-ctl stk" style="left:0"></td>${each(columns, column => column.key, (column, index) => html`<td
          class=${column().align === 'right' ? 'r num' : 'num'} class:stk=${isPinned(column)} class:s2=${isLastPin(column)} style:left=${pinStyle(column)}>${() => sums[column().key]
            ? valueText(sums[column().key]) : index() === 0 ? `Total · ${rows.get().length} ${rows.get().length === 1 ? 'line' : 'lines'}` : ''}</td>`)}</tr></tfoot>`)}
      </table>
    </div>
    <div class="g-under">${when(locked, () => derived && !read(props.lockedText ?? '') ? null : html`<span class="g-lock">${Icon('lock', { size: 'xs' })}${() => read(props.lockedText ?? 'Locked. Lines can’t change after the record is issued.')}</span>`,
      () => html`${Button({ icon: 'plus', label: 'Add row', size: 'small', className: 'g-add', disabled: () => !record.ready.get(), onClick: add })}${sectioned
        ? html`<span class="g-add-sec">or <button type="button" class="link-q" disabled=${() => !record.ready.get()} on:click=${addSection}>add a section</button></span>` : null}<span class="muted hide-sm">${Key('Tab')} next cell · ${Key('Enter')} cell below · ${Key('Esc')} cancel</span>`)}
      <span class="grow"></span>${when(() => summaryShown.get().length > 0, () => html`<div class="g-sum">${each(summaryShown, item => item.label, item => html`<div class:gt=${Boolean(item().grand)}><span>${item().label}</span><b class="num">${item().value}</b></div>`)}</div>`)}</div>
    ${props.heading?.footer ? when(focused, () => html`<div class="g-foot">${props.heading!.footer!()}</div>`) : null}
  </div>` as HTMLElement;
  scroller = grid.querySelector('.g-scroll') as HTMLElement;
  // Lines and section rows move by their drag handle: mouse, touch, or Space and the arrow keys.
  onCleanup(dragList(grid, { kind: 'line', move: (id, _zone, before) => moveLine(id, before),
    label: id => id.startsWith('s:') ? `Section ${table.sections.peek().find(section => section.key === id.slice(2))?.label ?? ''}` : `Row ${rows.peek().findIndex(row => row.key === id) + 1}` }));
  view.attach(scroller);
  const media = (() => { try { return window.matchMedia?.(NARROW) ?? null; } catch { return null; } })();
  const onMedia = () => narrow.set(narrowNow());
  media?.addEventListener?.('change', onMedia);
  onCleanup(() => media?.removeEventListener?.('change', onMedia));
  const width = () => grid.style.setProperty('--gw', `${scroller.clientWidth || 600}px`);
  width();
  // Measure the room the columns have; a hidden scroller (the row editor is open) keeps the last measure.
  const measure = () => { const now = scroller.clientWidth; if (now > 0 && now !== room.peek()) room.set(now); };
  const sizer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => measure()) : null;
  sizer?.observe(scroller);
  window.addEventListener('resize', measure);
  queueMicrotask(measure);
  onCleanup(() => { sizer?.disconnect(); window.removeEventListener('resize', measure); });
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
    // Esc closes the row editor first, then focus view.
    if (event.key === 'Escape' && expanded.peek() && !(event.target as Element).closest?.('.pop')) { event.preventDefault(); event.stopPropagation(); closeEditor(); return; }
    if (event.key === 'Escape' && focused.peek()) { event.preventDefault(); event.stopPropagation(); leaveFocus(); return; }
    // In the row editor, Alt and an arrow go to the previous or next row.
    if (expanded.peek() && event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown') && (event.target as Element).closest?.('.g-ed-main')) {
      event.preventDefault(); stepEditor(event.key === 'ArrowUp' ? -1 : 1); return;
    }
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
    if (event.key === 'Enter' || event.key === 'F2') { event.preventDefault(); startEdit(spot, undefined, true); return; }
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
    if (!startEdit(spot, undefined, true)) moveActive(spot);
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
