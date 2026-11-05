// List filters (docs/ui.md, Filters): rows of field, condition and value, combined with AND, as
// in Frappe's list filter. The field picker searches the model's fields and then each child
// table's ("Prices → Rate"). Rows on one child table form a group: one row of the table meets
// all of them (any row), or no row does (no row). The server checks every field, condition and
// value again (evlet/query.py) and refuses fields the role cannot read.
//
// The address keeps the rows (?f=) in the shape the server takes, with a few conditions the
// browser turns into server ones when it sends them: a time span becomes a range of days in
// GST, "is set" becomes isnull, "not equals" becomes "not in" so that empty values match too.

import { each, html, type Child } from '../dom';
import { legacyRecordId } from '../ids';
import { cell, derive, effect, untrack, type Cell, type Readable } from '../reactive';
import { Button, FilterChip, IconButton } from '../ui/base';
import { Dropdown, GroupedPicker, rangePresets, type GroupedChoice } from '../ui/pickers';
import { FieldControl, localField, type LinkSource } from '../ui/fields';
import { humanize } from '../ui/kinds';
import type { ChildSchema, FieldSchema, SchemaIndex } from '../model/schema';
import { formatOf, isStateField, type ScreenModel } from './meta';

export type Match = 'any' | 'none';
/** A row of the builder: field is a field name, or "table.field" for a child table's field. */
export interface FilterRow { id: number; field: string; op: string; value: unknown }
export interface FilterState { rows: FilterRow[]; matches: Record<string, Match> }
/** What the list routes take: an object of "field__operator" keys, or a list of conditions and child groups. */
export type ServerFilters = Record<string, unknown> | unknown[];

export interface FilterField { key: string; meta: FieldSchema; table: ChildSchema | null; label: string; group: string }

type ValueKind = 'none' | 'one' | 'two' | 'list' | 'span';
interface Condition { op: string; label: string; value: ValueKind; needs: string[] }
const condition = (op: string, label: string, value: ValueKind, needs: string[] = [op]): Condition => ({ op, label, value, needs });
const SET = [condition('set', 'is set', 'none', ['isnull']), condition('notset', 'is not set', 'none', ['isnull'])];
const TEXT = [condition('eq', 'equals', 'one'), condition('ne', 'not equals', 'one', ['nin']), condition('icontains', 'like', 'one'),
  condition('nicontains', 'not like', 'one'), ...SET, condition('in', 'in', 'list'), condition('nin', 'not in', 'list')];
const NUMBER = [condition('eq', '=', 'one'), condition('ne', '≠', 'one', ['nin']), condition('gt', '>', 'one'), condition('gte', '≥', 'one'),
  condition('lt', '<', 'one'), condition('lte', '≤', 'one'), condition('between', 'between', 'two'), ...SET];
const DATE = [condition('eq', 'on', 'one'), condition('lt', 'before', 'one'), condition('gt', 'after', 'one'), condition('between', 'between', 'two'),
  condition('timespan', 'timespan', 'span', ['between']), ...SET];
const MOMENT = [condition('lt', 'before', 'one'), condition('gt', 'after', 'one'), ...SET];
const LINK = [condition('eq', 'equals', 'one'), condition('ne', 'not equals', 'one', ['nin']), condition('in', 'in', 'list'), ...SET];
const CHOICE = [condition('in', 'in', 'list'), condition('nin', 'not in', 'list'), ...SET];
const CHECK = [condition('yes', 'is yes', 'none', ['eq']), condition('no', 'is no', 'none', ['eq'])];
const TAGS = [condition('contains', 'has', 'one'), ...SET];
const CONDITIONS: Record<string, Condition[]> = {
  text: TEXT, email: TEXT, url: TEXT, phone: TEXT, longtext: TEXT, richtext: TEXT,
  integer: NUMBER, decimal: NUMBER, currency: NUMBER, percent: NUMBER, duration: NUMBER,
  date: DATE, datetime: MOMENT, time: MOMENT, link: LINK, select: CHOICE, boolean: CHECK, tags: TAGS, multiselect: TAGS,
};
const TEXTUAL = new Set(['text', 'email', 'url', 'phone', 'longtext', 'richtext', 'tags']);

/** The conditions a field offers: its type's, less any the server does not take for it. */
export function conditionsFor(meta: FieldSchema): Condition[] {
  const list = CONDITIONS[meta.type] ?? [];
  return meta.operators ? list.filter(item => item.needs.every(op => meta.operators!.includes(op))) : list;
}

/** Named time spans, by key ("this_week"), from the date range presets. */
const spanKey = (label: string) => label.toLowerCase().replace(/ /g, '_');
export const timespans = () => rangePresets().map(preset => ({ key: spanKey(preset.label), label: preset.label, range: preset.range }));

const filterable = (model: ScreenModel | null, meta: FieldSchema) => !meta.hidden && (!model || !isStateField(model, meta))
  && (meta.operators ? meta.operators.length > 0 : meta.stored !== false) && conditionsFor(meta).length > 0;

/** The fields a list can be filtered on: the model's, then each child table's that the role can read. */
export function filterFields(index: SchemaIndex, model: ScreenModel): FilterField[] {
  const own = model.fields.filter(meta => filterable(model, meta)).map(meta => ({ key: meta.name, meta, table: null, label: meta.label, group: model.label }));
  const children = (model.children ?? []).filter(table => table.filterable !== false).flatMap(table => {
    const child = index.schema.models.find(item => item.name === table.model);
    return (child?.fields ?? []).filter(meta => filterable(null, meta) && meta.name !== table.link)
      .map(meta => ({ key: `${table.name}.${meta.name}`, meta, table, label: `${table.label} → ${meta.label}`, group: table.label }));
  });
  return [...own, ...children];
}

const filled = (value: unknown) => value !== null && value !== undefined && value !== '';

/** Whether a row has a field, a condition and the value that condition needs. */
export function complete(fields: Map<string, FilterField>, row: FilterRow): boolean {
  const field = fields.get(row.field), kind = field && conditionsFor(field.meta).find(item => item.op === row.op)?.value;
  if (!kind) return false;
  if (kind === 'none') return true;
  if (kind === 'one') return filled(row.value);
  if (kind === 'span') return timespans().some(span => span.key === row.value);
  return Array.isArray(row.value) && row.value.length > 0 && (kind === 'list' || (row.value.length === 2 && row.value.every(filled)));
}

/** One row as the server's [field, operator, value]. */
function serverCondition(name: string, row: FilterRow): unknown[] {
  switch (row.op) {
    case 'set': return [name, 'isnull', false];
    case 'notset': return [name, 'isnull', true];
    case 'yes': return [name, 'eq', true];
    case 'no': return [name, 'eq', false];
    case 'ne': return [name, 'nin', [row.value]];
    case 'timespan': return [name, 'between', timespans().find(span => span.key === row.value)!.range];
    default: return [name, row.op, row.value];
  }
}

/** The complete rows, grouped as the address and the server take them; `server` turns browser conditions into server ones. */
export function encode(fields: Map<string, FilterField>, state: FilterState, server: boolean): unknown[] {
  const result: unknown[] = [];
  const groups = new Map<string, unknown[]>();
  for (const row of state.rows) {
    if (!complete(fields, row)) continue;
    const field = fields.get(row.field)!;
    const name = field.table ? field.meta.name : row.field;
    const entry = server ? serverCondition(name, row) : [name, row.op, row.value];
    if (!field.table) { result.push(entry); continue; }
    if (!groups.has(field.table.name)) {
      groups.set(field.table.name, []);
      result.push({ child: field.table.name, match: state.matches[field.table.name] ?? 'any', where: groups.get(field.table.name) });
    }
    groups.get(field.table.name)!.push(entry);
  }
  return result;
}

/** A list of only equality conditions on distinct parent fields as the shorter object form. */
export function compact(list: unknown[]): ServerFilters {
  const object: Record<string, unknown> = {};
  for (const entry of list) {
    if (!Array.isArray(entry) || entry[1] !== 'eq' || entry[0] in object || entry[2] === null) return list;
    object[entry[0] as string] = entry[2];
  }
  return object;
}

/** Rows from the address: the list form, or the object form of earlier links ({"stage": "open"}). */
export function decode(fields: Map<string, FilterField>, text: string | undefined): FilterState {
  const state: FilterState = { rows: [], matches: {} };
  if (!text) return state;
  let id = 0;
  // Links saved before text ids hold numbers (42); they name the same records as '000000000001a'.
  const linkValue = (value: unknown): unknown => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? legacyRecordId(String(value))
    : Array.isArray(value) ? value.map(linkValue) : value;
  const add = (field: string, op: string, value: unknown) => {
    if (fields.get(field)?.meta.type === 'link') value = linkValue(value);
    const row = { id: ++id, field, op, value };
    if (complete(fields, row)) state.rows.push(row);
  };
  try {
    const raw = JSON.parse(text) as unknown;
    if (Array.isArray(raw)) {
      for (const entry of raw) {
        if (Array.isArray(entry) && entry.length === 3 && typeof entry[0] === 'string' && typeof entry[1] === 'string') add(entry[0], entry[1], entry[2]);
        else if (entry && typeof entry === 'object' && typeof entry.child === 'string' && Array.isArray(entry.where)) {
          if (entry.match === 'none') state.matches[entry.child] = 'none';
          for (const item of entry.where) if (Array.isArray(item) && item.length === 3) add(`${entry.child}.${item[0]}`, String(item[1]), item[2]);
        }
      }
    } else if (raw && typeof raw === 'object') {
      for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
        const range = value as { gte?: string; lte?: string } | null;
        if (range && typeof range === 'object' && !Array.isArray(range)) add(name, 'between', [range.gte, range.lte]);
        else if (fields.get(name)?.meta.type === 'boolean') add(name, value ? 'yes' : 'no', null);
        else if (fields.get(name)?.meta.type === 'select') add(name, 'in', [value]);
        else add(name, 'eq', value);
      }
    }
  } catch { /* an address that is not JSON opens the list unfiltered */ }
  return state;
}

// ---------- labels ----------

/** "Status in Draft, Sent", "Prices → Rate > 50" or "No Prices row: Rate > 50". */
export function rowLabel(fields: Map<string, FilterField>, state: FilterState, row: FilterRow, links: LinkSource): () => string {
  const field = fields.get(row.field)!, meta = field.meta;
  const found = conditionsFor(meta).find(item => item.op === row.op)!;
  const one = (value: unknown): string => {
    if (meta.type === 'link' && meta.target && typeof value === 'string') return links.title(meta.target, value).get() ?? `#${value}`;
    if (meta.type === 'select' || meta.type === 'multiselect') return humanize(String(value));
    return TEXTUAL.has(meta.type) ? String(value) : formatOf(meta, value) || String(value);
  };
  return () => {
    const value = found.value === 'none' ? '' : found.value === 'span' ? timespans().find(span => span.key === row.value)?.label ?? ''
      : found.value === 'two' ? (row.value as unknown[]).map(one).join(' and ') : found.value === 'list' ? (row.value as unknown[]).map(one).join(', ') : one(row.value);
    const text = `${field.table ? meta.label : field.label} ${found.label}${value ? ` ${value}` : ''}`;
    if (!field.table) return text;
    return state.matches[field.table.name] === 'none' ? `No ${field.table.label} row: ${text}` : `${field.table.label} → ${text}`;
  };
}

// ---------- the builder ----------

export interface FilterBuilder {
  state: Cell<FilterState>;
  fields: FilterField[];
  byKey: Map<string, FilterField>;
  /** The complete rows: what the chips, the badge and the address show. */
  active: Readable<FilterRow[]>;
  /** The complete rows as the server takes them (a list). */
  server: Readable<unknown[]>;
  /** The complete rows for ?f= in the address, or '' for none. */
  address: Readable<string>;
  add(): void;
  remove(id: number): void;
  clear(): void;
}

export function filterBuilder(index: SchemaIndex, model: ScreenModel, start: string | undefined): FilterBuilder {
  const fields = filterFields(index, model);
  const byKey = new Map(fields.map(field => [field.key, field]));
  const state = cell<FilterState>(decode(byKey, start), { name: `${model.name}.filters` });
  let next = Math.max(0, ...state.peek().rows.map(row => row.id));
  const active = derive(() => state.get().rows.filter(row => complete(byKey, row)));
  // Compared as text, so a change to a row that is not complete yet does not reload the list.
  const serverText = derive(() => JSON.stringify(encode(byKey, state.get(), true)));
  const server = derive(() => JSON.parse(serverText.get()) as unknown[]);
  const address = derive(() => { const list = encode(byKey, state.get(), false); return list.length ? JSON.stringify(list) : ''; });
  return {
    state, fields, byKey, active, server, address,
    add() { state.set({ ...state.peek(), rows: [...state.peek().rows, { id: ++next, field: '', op: '', value: null }] }); },
    remove(id) { state.set({ ...state.peek(), rows: state.peek().rows.filter(row => row.id !== id) }); },
    clear() { state.set({ rows: [], matches: {} }); },
  };
}

/** The active filters as removable chips, and Clear filters when there are any. */
export function FilterChips(builder: FilterBuilder, links: LinkSource): HTMLElement {
  return html`<span class="filters">${each(builder.active, row => row.id, row => FilterChip({
    label: () => rowLabel(builder.byKey, builder.state.get(), row(), links)(), onRemove: () => builder.remove(row().id) }))}${() => builder.active.get().length
    ? Button({ label: 'Clear filters', variant: 'ghost', size: 'small', onClick: () => builder.clear() }) : null}</span>` as HTMLElement;
}

/** The panel of filter rows: field, condition, value and remove; child table rows grouped by table. */
export function FilterPanel(builder: FilterBuilder, links: LinkSource, onClose: () => void): HTMLElement {
  const { state, byKey } = builder;
  const choices: GroupedChoice[] = builder.fields.map(field => ({ value: field.key, label: field.table ? field.meta.label : field.label, group: field.group, hint: humanize(field.meta.type) }));
  const update = (id: number, patch: Partial<FilterRow>) => state.set({ ...state.peek(), rows: state.peek().rows.map(row => row.id === id ? { ...row, ...patch } : row) });
  const tableOf = (row: FilterRow) => byKey.get(row.field)?.table?.name ?? '';
  const tables = derive(() => [...new Set(state.get().rows.map(tableOf))].filter(Boolean).join(','));
  const pickers = new Map<number, HTMLButtonElement & { open(): void }>();

  const rowView = (row: () => FilterRow) => {
    const id = untrack(row).id;
    const field = derive(() => byKey.get(row().field) ?? null);
    const found = derive(() => field.get() ? conditionsFor(field.get()!.meta).find(item => item.op === row().op) ?? null : null);
    const picker = GroupedPicker({ value: () => row().field || null, choices, label: 'Field', placeholder: 'Choose a field', className: 'fb-field',
      onChange: key => { const first = conditionsFor(byKey.get(key)!.meta)[0]; update(id, { field: key, op: first.op, value: null }); } });
    pickers.set(id, picker);
    const condition = () => field.get() ? Dropdown({ value: () => row().op || null, ariaLabel: 'Condition', className: 'fb-op',
      choices: () => conditionsFor(field.get()!.meta).map(item => ({ value: item.op, label: item.label })),
      onChange: op => { const before = found.peek()?.value, after = conditionsFor(field.peek()!.meta).find(item => item.op === op)?.value;
        update(id, { op: op ?? '', value: before === after ? untrack(row).value : null }); } }) : html`<span class="fb-op"></span>`;
    // The value input is built again only when the field or the kind of value changes, so typing keeps focus.
    const editorKey = derive(() => `${row().field}|${found.get()?.value ?? ''}`);
    const editor = () => { editorKey.get(); return untrack(() => field.peek() && found.peek() ? ValueEditor(field.peek()!, found.peek()!.value, () => row().value, value => update(id, { value }), links) : null); };
    return html`<div class="fb-row" data-field=${() => row().field}>${picker}${() => { field.get(); return untrack(condition); }}<div class="fb-value">${editor}</div>${IconButton({
      icon: 'x', tip: 'Remove filter', size: 'small', onClick: () => builder.remove(id) })}</div>`;
  };
  const rowsOf = (table: string) => derive(() => state.get().rows.filter(row => tableOf(row) === table));
  const group = (name: string) => {
    const table = builder.fields.find(field => field.table?.name === name)!.table!;
    return html`<div class="fb-group" role="group" aria-label=${`${table.label} rows`}><div class="fb-gh">${Dropdown({ ariaLabel: `${table.label}: which rows`, className: 'fb-match',
      value: () => state.get().matches[name] ?? 'any', choices: () => [{ value: 'any', label: `Any ${table.label} row` }, { value: 'none', label: `No ${table.label} row` }],
      onChange: value => state.set({ ...state.peek(), matches: { ...state.peek().matches, [name]: (value ?? 'any') as Match } }) })}<span class="muted">meets all of these</span></div>
      ${each(rowsOf(name), row => row.id, rowView)}</div>`;
  };
  const add = () => { builder.add(); const id = state.peek().rows[state.peek().rows.length - 1].id; queueMicrotask(() => pickers.get(id)?.open()); };
  const panel = html`<section class="fb" aria-label="Filters">
    <p class="muted fb-empty" hidden=${() => state.get().rows.length > 0}>No filters yet. Add one to narrow the list; every filter must match.</p>
    <div class="fb-rows">${each(rowsOf(''), row => row.id, rowView)}</div>
    ${() => tables.get() ? untrack(() => tables.peek().split(',').map(group)) : null}
    <div class="fb-foot">${Button({ label: 'Add filter', icon: 'plus', size: 'small', onClick: add })}<span class="grow"></span>${Button({ label: 'Clear filters', variant: 'ghost', size: 'small',
      onClick: () => builder.clear() })}${Button({ label: 'Done', size: 'small', onClick: onClose })}</div></section>` as HTMLElement;
  if (!state.peek().rows.length) queueMicrotask(add);
  return panel;
}

/** A blank field of the same type for a value input: no rules, limits or calculation of the record's field. */
function inputMeta(meta: FieldSchema, suffix: string): FieldSchema {
  return { ...meta, name: `${meta.name}-${suffix}`, required: false, read_only: false, compute: null, initial: null, applies_when: null,
    required_when: null, locked_when: null, minimum: null, maximum: null, help_text: undefined, tooltip: undefined, default: undefined, currency_source: undefined };
}

/** One value through the field's own control: the date picker, the currency input, the record picker. */
function OneValue(meta: FieldSchema, value: () => unknown, set: (value: unknown) => void, links: LinkSource, label: string, suffix = 'value'): Child {
  if (TEXTUAL.has(meta.type)) {
    const input = html`<input class="input" aria-label=${label} placeholder="Value" autocomplete="off"
      on:change=${(event: Event) => set((event.target as HTMLInputElement).value.trim() || null)}>` as HTMLInputElement;
    input.value = String(untrack(value) ?? '');
    return input;
  }
  if (meta.type === 'multiselect') return Dropdown({ value: () => (value() as string | null) ?? null, ariaLabel: label, choices: () => (meta.choices ?? []).map(choice => ({ value: choice, label: humanize(choice) })), onChange: set });
  const field = localField(inputMeta(meta, suffix), untrack(value));
  effect(() => { const next = field.value.get(); if (JSON.stringify(next ?? null) !== JSON.stringify(untrack(value) ?? null)) set(next); });
  return FieldControl(field, { links, label }).element;
}

function ValueEditor(field: FilterField, kind: ValueKind, value: () => unknown, set: (value: unknown) => void, links: LinkSource): Child {
  const meta = field.meta, label = `${field.label} value`;
  if (kind === 'none') return null;
  if (kind === 'span') return Dropdown({ value: () => (value() as string | null) ?? null, ariaLabel: 'Time span', placeholder: 'Choose a span',
    choices: () => timespans().map(span => ({ value: span.key, label: span.label })), onChange: set });
  if (kind === 'one') return OneValue(meta, value, set, links, label);
  if (kind === 'two') {
    const part = (at: number) => () => (Array.isArray(value()) ? (value() as unknown[])[at] : null) ?? null;
    const put = (at: number) => (next: unknown) => { const both = Array.isArray(untrack(value)) ? [...(untrack(value) as unknown[])] : [null, null]; both[at] = next; set(both); };
    return html`<div class="fb-two">${OneValue(meta, part(0), put(0), links, `${field.label} from`, 'from')}<span class="muted">and</span>${OneValue(meta, part(1), put(1), links, `${field.label} to`, 'to')}</div>`;
  }
  // A list: choices to tick, records to add one by one, or values separated by commas.
  const list = () => (Array.isArray(value()) ? value() as unknown[] : []);
  if (meta.type === 'select') {
    const field = localField({ ...inputMeta(meta, 'list'), type: 'multiselect' }, untrack(list).length ? untrack(list) : null);
    effect(() => { const next = field.value.get() as unknown[] | null; if (JSON.stringify(next ?? []) !== JSON.stringify(untrack(list))) set(next ?? []); });
    return FieldControl(field, { links, label }).element;
  }
  if (meta.type === 'link') {
    // The picker adds one record at a time and is built again empty after each.
    const added = cell(0);
    const picker = () => { added.get(); return untrack(() => OneValue(meta, () => null, id => {
      if (id !== null && !untrack(list).includes(id)) set([...untrack(list), id]);
      added.set(added.peek() + 1);
    }, links, `Add ${field.label}`, 'add')); };
    return html`<div class="fb-list">${() => list().map(id => FilterChip({ label: () => (meta.target ? links.title(meta.target, String(id)).get() : null) ?? `#${id}`,
      onRemove: () => set(untrack(list).filter(item => item !== id)) }))}${picker}</div>`;
  }
  const input = html`<input class="input" aria-label=${label} placeholder="Values, separated by commas" autocomplete="off"
    on:change=${(event: Event) => set((event.target as HTMLInputElement).value.split(',').map(item => item.trim()).filter(Boolean))}>` as HTMLInputElement;
  input.value = untrack(list).join(', ');
  return input;
}
