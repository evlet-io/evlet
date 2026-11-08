// The blocks of a declared record layout (Meta.layout with evlet.Tab, Related, Html, Chart and Table field
// names; docs/ui.md, Record layouts). Sections and tables are drawn by the record page; this file draws the
// rest. Related rows come from the list route and charts from the aggregate route, with the block's filters
// and this record's id, so the server applies the role's row scope and field permissions as for any list.
// Nothing is asked for until a block scrolls into view, and a save of the record loads shown blocks again.

import { html, when, type Child } from '../dom';
import { ApiError, type RecordData } from '../api';
import { batch, cell, effect, untrack, type Readable } from '../reactive';
import type { FieldSchema, LayoutBlock, LayoutCondition, LayoutTab, RecordLayout } from '../model/schema';
import { IconButton } from '../ui/base';
import { plotChart } from '../ui/chart';
import { RichTextView } from '../ui/fields';
import { humanize } from '../ui/kinds';
import { DataTable, type Column } from '../ui/table';
import type { AppContext } from './context';
import { decode, filterFields } from './filters';
import { cellView } from './list-page';
import { fieldOf, formatOf, listFields, measureLabel, measureText, measureView, titleOf, type ScreenModel } from './meta';
import { rangeText, whenVisible } from './related-data';

export type RelatedBlock = Extract<LayoutBlock, { kind: 'related' }>;
export type ChartBlock = Extract<LayoutBlock, { kind: 'chart' }>;
export type HtmlBlock = Extract<LayoutBlock, { kind: 'html' }>;

/** What a block reads of the record it is on. */
export interface BlockRecord {
  id: string;
  /** A field's saved value: filters that compare with the record read these. */
  saved(field: string): unknown;
  /** A field's value as shown now, unsaved edits included: Html placeholders read these. */
  current(field: string): unknown;
  /** Changes after each save (the record's etag), so shown blocks load again. */
  version: Readable<unknown>;
}

// ---------- the layout ----------

/** The record page tab id of a layout Tab: Details keeps its id, any other is t-<name> like a Table's. */
export const layoutTabId = (tab: Pick<LayoutTab, 'name'>) => tab.name === 'details' ? 'details' : `t-${tab.name}`;

/** Every block of a layout, in order, across its Tabs. */
export const layoutBlocks = (layout: RecordLayout): LayoutBlock[] => layout.tabs ? layout.tabs.flatMap(tab => tab.blocks) : layout.blocks ?? [];

/** Table field names a layout places as blocks: they get no tab of their own. */
export const placedTables = (layout: RecordLayout | undefined) => new Set(layout ? layoutBlocks(layout).flatMap(block => block.kind === 'table' ? [block.name] : []) : []);

/** Whether a layout lists related records itself: then the automatic Related tab and side block are off, unless
 * Meta.tabs or Meta.side_blocks names 'related'. */
export const declaresRelated = (layout: RecordLayout | undefined) => Boolean(layout && layoutBlocks(layout).some(block => block.kind === 'related'));

// ---------- filters ----------

const isRef = (value: unknown): value is { $expr: 'ref'; args: [string] } =>
  Boolean(value && typeof value === 'object' && (value as { $expr?: unknown }).$expr === 'ref' && Array.isArray((value as { args?: unknown }).args));

/**
 * The block's conditions for the list and aggregate routes: its Link equal to this record, then its filters,
 * each Ref('field') replaced by that field's saved value. Null when nothing can match: a gt, gte, lt or lte
 * against a field of the record that is empty.
 */
export function blockFilters(block: { link: string; filters: LayoutCondition[] }, record: Pick<BlockRecord, 'id' | 'saved'>): LayoutCondition[] | null {
  const result: LayoutCondition[] = [[block.link, 'eq', record.id]];
  for (const [field, op, value] of block.filters) {
    if (!isRef(value)) { result.push([field, op, value]); continue; }
    const saved = record.saved(value.args[0]);
    const empty = saved === null || saved === undefined || saved === '';
    if (empty && op !== 'eq' && op !== 'ne') return null;
    result.push([field, op, empty ? null : saved]);
  }
  return result;
}

/** A server condition as the list address's filter row ([field, op, value]), or null when the filter panel has no such row. */
function addressRow(meta: FieldSchema | undefined, [field, op, value]: LayoutCondition): LayoutCondition | null {
  if (!meta) return null;
  if (op === 'isnull') return [field, value ? 'notset' : 'set', null];
  if (meta.type === 'boolean' && op === 'eq' && typeof value === 'boolean') return [field, value ? 'yes' : 'no', null];
  if (op === 'eq' && value === null) return [field, 'notset', null];
  if (meta.type === 'select' || meta.type === 'state') return op === 'eq' ? [field, 'in', [value]] : op === 'ne' ? [field, 'nin', [value]] : [field, op, value];
  return [field, op, value];
}

/** The list of the block's model with the same filters (?f=), or null when its filter panel cannot hold them all,
 * so View all never shows more than the block does. */
export function viewAllHref(ctx: AppContext, target: ScreenModel, conditions: LayoutCondition[]): string | null {
  const fields = new Map(filterFields(ctx.schema, target).map(field => [field.key, field]));
  const rows = conditions.map(condition => addressRow(fields.get(condition[0])?.meta, condition));
  if (rows.some(row => row === null)) return null;
  const text = JSON.stringify(rows);
  if (decode(fields, text).rows.length !== rows.length) return null;
  return ctx.router.href('list', { model: target.name }, { f: text });
}

const problemText = (error: unknown) => error instanceof ApiError ? error.message : 'Not loaded.';

/** Loads once the element first scrolls into view, and again after each save while it has loaded. */
function loadWhenShown(element: Element, version: Readable<unknown>, load: () => void) {
  let shown = false;
  whenVisible(element, () => { shown = true; load(); });
  let first = true;
  effect(() => { version.get(); if (first) { first = false; return; } if (shown) untrack(load); });
}

const target = (ctx: AppContext, name: string) => ctx.schema.schema.models.find(model => model.name === name) as unknown as ScreenModel | undefined;

// ---------- Related ----------

/** Records of another model that point to this one, narrowed by the block's filters: a page of `limit` rows,
 * a pager, and View all for the same list. */
export function RelatedRecords(ctx: AppContext, block: RelatedBlock, record: BlockRecord): Child {
  const model = target(ctx, block.model);
  if (!model) return null;
  const rows = cell<RecordData[]>([]), total = cell<number | null>(null), offset = cell(0), problem = cell('');
  const conditions = () => blockFilters(block, record);
  let asked = 0;
  const load = (start = offset.peek()) => {
    const filters = conditions();
    if (!filters) { batch(() => { rows.set([]); total.set(0); problem.set(''); }); return; }
    const ticket = ++asked;
    offset.set(start);
    void ctx.api.list(model.name, { limit: block.limit, offset: start, filters, orderBy: block.order.join(',') }).then(page => {
      if (ticket !== asked) return;
      const count = (page as { total?: number }).total ?? page.items.length;
      // Records went away under the page shown: step back to the last page that has rows.
      if (!page.items.length && start > 0 && count > 0) { load(Math.floor((count - 1) / block.limit) * block.limit); return; }
      batch(() => { rows.set(page.items); total.set(count); problem.set(''); });
    }, error => { if (ticket === asked) problem.set(problemText(error)); });
  };
  const named = block.fields.map(name => fieldOf(model, name)).filter((meta): meta is FieldSchema => Boolean(meta && !meta.hidden));
  const shown = (named.length ? named : listFields(model).filter(meta => meta.name !== block.link).slice(0, 4));
  const title = fieldOf(model, model.title_field);
  const columns: Column<RecordData>[] = shown.map((meta, index) => ({
    key: meta.name, label: meta.label, width: index === 0 ? 220 : 150, value: (row: RecordData) => row[meta.name],
    align: ['currency', 'decimal', 'integer', 'percent'].includes(meta.type) ? 'right' : 'left', sortable: false,
    // The first column opens the record: its title when the block shows the title field.
    render: index === 0 ? (row: () => RecordData) => html`<a class="ev-row-link" href=${() => ctx.router.href('record', { model: model.name, id: String(row().id) })}>${
      () => meta === title || !title ? titleOf(model, row()) : formatOf(meta, row()[meta.name]) || titleOf(model, row())}</a>` : (row: () => RecordData) => cellView(ctx, model, meta, row),
  }));
  const none = `No ${block.label.toLowerCase()}.`;
  const all = () => { const filters = conditions(); return filters ? viewAllHref(ctx, model, filters) : null; };
  const pages = () => (total.get() ?? 0) > block.limit;
  const section = html`<section class="panel ev-related ev-block" aria-label=${block.label}><div class="panel-h"><h3>${block.label}</h3><span class="muted num">${() => total.get() ? String(total.get()) : ''}</span>
      ${when(() => Boolean(total.get() && all()), () => html`<a class="ev-view-all" href=${all()!} aria-label=${`View all ${block.label.toLowerCase()}`}>View all</a>`)}</div>
    ${() => problem.get() ? html`<p class="muted ev-block-note">${problem.get()}</p>` : null}
    ${when(() => total.get() === null && !problem.get(), () => html`<p class="muted ev-block-note" role="status">Loading…</p>`)}
    ${when(() => total.get() === 0 && !problem.get(), () => html`<p class="ev-related-empty">${none}</p>`)}
    ${when(() => Boolean(total.get()), () => DataTable<RecordData>({ columns, rows, key: row => row.id, label: block.label,
      onOpen: row => void ctx.router.go('record', { model: model.name, id: String(row.id) }), height: Math.min(300, 44 * (block.limit + 1)), empty: () => html`<span class="muted">${none}</span>` }))}
    ${when(pages, () => html`<div class="ev-pager"><span class="muted num">${() => rangeText(offset.get(), rows.get().length, total.get() ?? 0)}</span>
      ${IconButton({ icon: 'chevron-left', tip: 'Previous page', size: 'small', disabled: () => offset.get() === 0, onClick: () => load(Math.max(0, offset.peek() - block.limit)) })}
      ${IconButton({ icon: 'chevron-right', tip: 'Next page', size: 'small', disabled: () => offset.get() + block.limit >= (total.get() ?? 0), onClick: () => load(offset.peek() + block.limit) })}</div>`)}</section>` as Element;
  loadWhenShown(section, record.version, () => load());
  return section;
}

// ---------- Html ----------

const PLACEHOLDER = /\{\{|\}\}|\{([a-z_][a-z0-9_]*)\}/g;

/** A field's value as text for an Html placeholder: formatted, a Link as its record's title, empty when unset or unreadable. */
function placeholderText(ctx: AppContext, model: ScreenModel, name: string, value: unknown): string {
  const meta = fieldOf(model, name);
  if (!meta || value === null || value === undefined || value === '') return '';
  if (meta.type === 'link' && meta.target) return ctx.links.title(meta.target, String(value)).get() ?? '…';
  if (meta.type === 'select' || meta.type === 'state') return humanize(String(value));
  return formatOf(meta, value);
}

/** Replaces {field} in the text nodes under `root` with text: values never become markup. */
function fillPlaceholders(root: Node, text: (name: string) => string) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node as Text);
  for (const node of nodes) {
    const source = node.data;
    if (!source.includes('{') && !source.includes('}')) continue;
    node.data = source.replace(PLACEHOLDER, (match, name: string | undefined) => match === '{{' ? '{' : match === '}}' ? '}' : text(name!));
  }
}

/** The app's static content, kept to the rich text allowlist by the server and parsed here as rich text again;
 * `{field}` shows that field of the record as text. */
export function HtmlContent(ctx: AppContext, model: ScreenModel, block: HtmlBlock, record: Pick<BlockRecord, 'current'>): Child {
  return html`<div class="ev-block ev-html">${() => {
    const view = RichTextView(block.html);
    fillPlaceholders(view, name => placeholderText(ctx, model, name, record.current(name)));
    return view;
  }}</div>`;
}

// ---------- Chart ----------

/** The label of a group key: a Select's choice, a Link's title, a month as it is. */
function groupText(ctx: AppContext, model: ScreenModel, key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'None';
  const meta = fieldOf(model, key);
  if (!meta) return String(value);
  return placeholderText(ctx, model, key, value) || String(value);
}

/** Declared measures of the block's model over the records that point to this one: stat numbers, or bars or a
 * line by the block's x field. The numbers are the aggregate route's, for this role. */
export function MeasureChart(ctx: AppContext, block: ChartBlock, record: BlockRecord): Child {
  const model = target(ctx, block.model);
  if (!model) return null;
  const items = cell<Array<Record<string, unknown>> | null>(null), problem = cell('');
  const measures = block.y.map(name => (model.measures ?? []).find(item => item.name === name)).filter(item => item !== undefined);
  if (!measures.length) return null;
  const [field, part] = (block.x ?? '').split(':');
  const key = part ? `${field}__${part}` : field;
  let asked = 0;
  const load = () => {
    const filters = blockFilters(block, record);
    if (!filters) { batch(() => { items.set([]); problem.set(''); }); return; }
    const ticket = ++asked;
    void ctx.api.aggregate(model.name, measures.map(item => item.name), block.x ? [block.x] : [], filters).then(result => {
      if (ticket === asked) batch(() => { items.set(Array.isArray(result.items) ? result.items : [result.items as Record<string, unknown>]); problem.set(''); });
    }, error => { if (ticket === asked) problem.set(problemText(error)); });
  };
  const label = block.label ?? measures.map(measureLabel).join(' and ');
  const value = (row: Record<string, unknown>, name: string) => measureText(ctx.schema, model, measures.find(item => item.name === name)!, row[name]);
  const body = () => {
    const rows = items.get();
    if (problem.get()) return html`<p class="muted ev-block-note">${problem.get()}</p>`;
    if (rows === null) return html`<p class="muted ev-block-note" role="status">Loading…</p>`;
    if (block.type === 'stat') {
      const row = rows[0] ?? {};
      return html`<dl class="ev-stats">${measures.map(item => html`<div class="ev-stat"><dt>${measureLabel(item)}</dt><dd class="num">${measureView(ctx.schema, model, item, row[item.name])}</dd></div>`)}</dl>`;
    }
    if (!rows.length) return html`<p class="ev-related-empty">Nothing to chart yet.</p>`;
    const host = html`<div class="ev-report-chart-plot"></div>` as HTMLElement;
    host.append(plotChart<Record<string, unknown>>({ type: block.type as 'bar' | 'line', rows, title: `${label} chart`, label: row => groupText(ctx, model, field, row[key]),
      series: measures.map(item => ({ label: measureLabel(item), value: row => Number(row[item.name]) || 0, text: row => value(row, item.name) })) }));
    return host;
  };
  const section = html`<section class=${`ev-report-chart ev-block ev-chart-${block.type}`} aria-label=${label}><div class="ev-report-chart-h"><h3>${label}</h3>
    <span class="ev-report-legend">${measures.length > 1 && block.type !== 'stat' ? measures.map((item, s) => html`<span><i class=${`sw s${s}`}></i>${measureLabel(item)}</span>`) : null}</span></div>
    ${body}</section>` as Element;
  loadWhenShown(section, record.version, load);
  return section;
}
