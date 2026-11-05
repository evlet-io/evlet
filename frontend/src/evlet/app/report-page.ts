// Reports (evlet.Report, docs/reports.md): the index of reports this role may run, and one report's
// view. Its filters are a transient form the server checks after each change (POST .../evaluate);
// editing them never runs a report unless it is an autorun report. A run shows the filters it ran
// with, one page of typed rows, totals (of every row, or of the page when the report gives none),
// an optional chart, Export (CSV) and Print (Print Design's report PDF).
//
// The filters it ran with, the sort and the page live in the address, so a link opens the same view.
// Sorting and paging run again with those filters, never with unsent edits. Everything is checked on
// the server: the role, the report's sortable columns, and the fields each person may read.

import { html, match, when, type Child } from '../dom';
import { ApiError } from '../api';
import { batch, cell, derive, effect, onCleanup, untrack } from '../reactive';
import type { FieldSchema, ReportColumn, ReportSchema } from '../model/schema';
import { formatDate, formatMoney, formatNumber } from '../model/format';
import { Button, Checkbox, EmptyState, IconButton, IconTile } from '../ui/base';
import { fieldFor, localField } from '../ui/fields';
import { humanize } from '../ui/kinds';
import { copyText, DialogFooter, openDrawer, openModal, openPopover, toast } from '../ui/layers';
import { PageHeader, Toolbar } from '../ui/list';
import { ColumnsMenu, DataTable, emptyPrefs, type Column, type Sort, type TablePrefs } from '../ui/table';
import type { ReportPage as Page } from './client';
import type { AppContext } from './context';

export const REPORT_ICON = 'chart-column';
const PAGE = 50;
const NUMBERS = ['integer', 'decimal', 'currency'];
const CODE = /^[A-Z]{3}$/;

/** A row of the table: a report row, or a group's heading or subtotal placed among them. */
type Row = Record<string, unknown> & { _row: number | string; _kind?: 'head' | 'sub'; _group?: GroupItem };
type GroupItem = { value: unknown; count: number; totals: Record<string, string | null> };
/** A saved view: the address of a report's filters and sort, under a name. Kept per person on the device. */
type SavedView = { name: string; query: Record<string, string> };
const MAX_VIEWS = 20;
type Local = ReturnType<typeof localField>;
type Rule = { visible?: boolean; required?: boolean; read_only?: boolean };

/** The reports this role may run, from the schema. */
export const reportsOf = (ctx: AppContext): ReportSchema[] => ctx.schema.schema.reports ?? [];

/** The reports a model's list offers. */
export const reportsFor = (ctx: AppContext, model: string) => reportsOf(ctx).filter(report => report.model === model);

/** Reports by sidebar heading, in declaration order; reports without a group come first. */
export function reportGroups(reports: ReportSchema[]): Array<[string, ReportSchema[]]> {
  const groups = new Map<string, ReportSchema[]>();
  for (const report of reports) groups.set(report.group, [...(groups.get(report.group) ?? []), report]);
  return [...groups].sort(([a], [b]) => (a ? 1 : 0) - (b ? 1 : 0));
}

const columnLabel = (column: ReportColumn) => column.label || humanize(column.name);

// ---------- the index ----------

/** /reports: every report this role may run, by group. */
export function ReportsPage(ctx: AppContext): HTMLElement {
  const reports = reportsOf(ctx);
  ctx.crumbs.set([{ label: 'Reports' }]);
  const card = (report: ReportSchema) => html`<a class="ev-report-card" href=${ctx.router.href('report', { report: report.name })}>
    ${IconTile(REPORT_ICON, { tint: 'rose', size: 32 })}<span><b>${report.label}</b>
    <small class="muted">${[`${report.columns.length} columns`, report.fields.filter(field => !field.hidden).length ? `${report.fields.filter(field => !field.hidden).length} filters` : 'No filters',
      report.autorun ? 'Runs as you filter' : ''].filter(Boolean).join(' · ')}</small></span></a>`;
  return html`<div class="page ev-reports">${PageHeader({ icon: REPORT_ICON, tint: 'rose', title: 'Reports', count: reports.length })}
    ${reports.length ? reportGroups(reports).map(([group, list]) => html`<section class="ev-report-group">${group ? html`<h2>${group}</h2>` : null}
      <div class="ev-report-cards">${list.map(card)}</div></section>`)
      : EmptyState({ icon: REPORT_ICON, tint: 'slate', title: 'No reports', text: 'Your role cannot run any report here.' })}</div>` as HTMLElement;
}

// ---------- values ----------

/** A value from the address, typed for its field. Lists and objects travel as JSON. */
function fromAddress(meta: FieldSchema, text: string): unknown {
  if (text.startsWith('[') || text.startsWith('{')) { try { return JSON.parse(text); } catch { return null; } }
  if (meta.type === 'boolean') return text === 'true';
  // Record ids stay text (13 characters); only whole numbers become numbers.
  if (meta.type === 'integer' && /^-?\d{1,15}$/.test(text)) return Number(text);
  return text;
}

/** A filter's value before anyone changes it: its declared default. */
const initial = (meta: FieldSchema) => meta.default_kind === 'static' ? meta.default ?? null : meta.type === 'boolean' ? false : null;
const toAddress = (value: unknown) => typeof value === 'object' ? JSON.stringify(value) : String(value);
const blank = (value: unknown) => value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length);

/** A cell's text: numbers grouped with their unit, dates in the person's format, links by their title. */
export function cellText(ctx: AppContext, column: ReportColumn, value: unknown): string {
  if (blank(value)) return '';
  switch (column.type) {
    case 'currency': return column.unit && CODE.test(column.unit) ? formatMoney(value as string, { currency: column.unit }) : `${formatNumber(value as string, 2)}${column.unit ? ` ${column.unit}` : ''}`;
    case 'decimal': { const scale = String(value).split('.')[1]?.length ?? 0; return formatNumber(value as string, Math.min(scale, 6)); }
    case 'integer': return formatNumber(value as number, 0);
    case 'date': return formatDate(String(value));
    case 'boolean': return value ? 'Yes' : 'No';
    case 'link': return column.link ? ctx.links.title(column.link, String(value)).get() ?? String(value) : String(value);
    default: return String(value);
  }
}

// ---------- one report ----------

export function ReportPage(ctx: AppContext, name: string): HTMLElement {
  const { api, router } = ctx;
  const report = reportsOf(ctx).find(item => item.name === name)!;
  const start = router.route.peek()?.query ?? {};
  ctx.crumbs.set([{ label: 'Reports', href: router.href('reports', {}) }, ...(report.group ? [{ label: report.group }] : []), { label: report.label }]);

  // ---------- filters ----------

  const fields = new Map<string, Local>();
  for (const meta of report.fields) {
    const { calculated, ...rest } = meta;
    const given = start[meta.name];
    const value = given !== undefined ? fromAddress(meta, given) : initial(meta);
    fields.set(meta.name, localField({ ...rest, ...(calculated ? { compute: { $expr: 'server' } } : {}) } as FieldSchema, value));
  }
  const fromLink = report.fields.some(meta => start[meta.name] !== undefined) || start.run === '1';
  const touched = new Set<string>(report.fields.filter(meta => start[meta.name] !== undefined).map(meta => meta.name));
  const ready = cell(false, { name: `${name}.ready` });
  const checking = cell(true, { name: `${name}.checking` });
  const note = cell('', { name: `${name}.note` });
  const payload = () => Object.fromEntries([...fields].filter(([, field]) => !field.calculated && !field.meta.read_only).map(([key, field]) => [key, field.value.peek()]));
  const edited = cell(JSON.stringify(payload()), { name: `${name}.edited` });

  let checkTicket = 0;
  const check = async () => {
    const mine = ++checkTicket;
    checking.set(true);
    try {
      const result = await api.evaluateReport(name, payload(), mine);
      if (mine !== checkTicket) return;
      batch(() => {
        for (const [key, field] of fields) {
          const value = result.values[key];
          if (value !== undefined && (field.calculated || field.meta.read_only || !touched.has(key))) field.value.set(value);
          const rule: Rule = result.rules?.[key] ?? {};
          field.rules.set({ applies: rule.visible ?? true, required: rule.required ?? field.meta.required, locked: rule.read_only ?? false });
          field.error.set(result.errors[key] ?? (touched.has(key) && result.states[key] === 'required-input' ? 'This field is required' : null));
        }
        const others = Object.entries(result.errors).filter(([key]) => !fields.has(key)).map(([key, message]) => key === '_form' ? message : `${humanize(key)}: ${message}`);
        note.set(others.join(' '));
        ready.set(!Object.keys(result.errors).length && !Object.values(result.states).includes('required-input'));
        edited.set(JSON.stringify(payload()));
        checking.set(false);
      });
    } catch (error) {
      if (mine !== checkTicket) return;
      batch(() => { note.set(error instanceof ApiError ? error.message : 'The filters could not be checked.'); ready.set(false); checking.set(false); });
    }
  };

  // ---------- results ----------

  const result = cell<Page | null>(null, { name: `${name}.result` });
  const executed = cell<Record<string, unknown> | null>(null, { name: `${name}.executed` });
  const busy = cell(false), failure = cell<string | null>(null);
  const sortFrom = (value: string | undefined): Sort => !value ? null : value.startsWith('-') ? { key: value.slice(1), dir: 'desc' } : { key: value, dir: 'asc' };
  const sort = cell<Sort>(start.sort && report.sortable.includes(start.sort.replace(/^-/, '')) ? sortFrom(start.sort) : null, { name: `${name}.sort` });
  const page = cell(Math.max(1, Number(start.page) || 1), { name: `${name}.page` });
  const sortText = () => { const order = sort.peek(); return order ? `${order.dir === 'desc' ? '-' : ''}${order.key}` : null; };
  const dataRows = derive<Row[]>(() => (result.get()?.rows ?? []).map((row, index) => ({ ...row, _row: (result.get()?.offset ?? 0) + index })));
  // With group_by, each run of rows with the same group value gets a heading above and a subtotal below.
  const grouping = report.group_by;
  const rows = derive<Row[]>(() => {
    const list = dataRows.get(), groups = result.get()?.groups;
    if (!grouping || !groups) return list;
    const items = new Map(groups.items.map(item => [JSON.stringify(item.value), item]));
    const keyOf = (row: Row | undefined) => row ? JSON.stringify(row[grouping]) : undefined;
    return list.flatMap((row, index) => {
      const key = keyOf(row)!, group = items.get(key);
      const head: Row[] = keyOf(list[index - 1]) !== key ? [{ [grouping]: row[grouping], _row: `head:${index}`, _kind: 'head', _group: group }] : [];
      const sub: Row[] = keyOf(list[index + 1]) !== key ? [{ [grouping]: row[grouping], _row: `sub:${index}`, _kind: 'sub', _group: group }] : [];
      return [...head, row, ...sub];
    });
  });
  const changed = derive(() => executed.get() !== null && JSON.stringify(executed.get()) !== edited.get());

  const leaving = new AbortController();
  onCleanup(() => leaving.abort());
  let runTicket = 0;
  let quiet = false;
  const run = async (filters: Record<string, unknown>, at = 1) => {
    const mine = ++runTicket;
    busy.set(true);
    try {
      const answer = await api.runReport(name, { filters, limit: PAGE, offset: (at - 1) * PAGE, sort: sortText(), revision: mine }, leaving.signal);
      if (mine !== runTicket) return;
      batch(() => { result.set(answer); executed.set(filters); page.set(at); failure.set(null); busy.set(false); });
    } catch (error) {
      if (mine !== runTicket || (error instanceof ApiError && error.kind === 'aborted')) return;
      batch(() => {
        busy.set(false);
        if (error instanceof ApiError && error.kind === 'validation' && Object.keys(error.fields).some(key => fields.has(key))) {
          for (const [key, message] of Object.entries(error.fields)) fields.get(key)?.error.set(message);
          note.set('Fix the marked filters to run this report.');
        } else failure.set(error instanceof ApiError ? error.message : 'The report could not be run.');
      });
    }
  };
  const runNow = () => {
    for (const key of fields.keys()) touched.add(key);
    if (!ready.peek()) { void check().then(() => { if (ready.peek()) void run(payload()); }); return; }
    void run(payload());
  };
  // Sorting and paging run again with the filters the report ran with.
  const again = (at: number) => { const filters = executed.peek(); if (filters) void run(filters, at); };
  let first = true;
  effect(() => { sort.get(); if (first) { first = false; return; } if (!quiet) untrack(() => again(1)); });

  // The first check settles the defaults; an autorun report, or a shared link, then runs.
  let autoTimer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => clearTimeout(autoTimer));
  void check().then(() => { if (ready.peek() && (report.autorun || fromLink)) void run(payload(), fromLink ? page.peek() : 1); });
  const commit = (key: string) => () => {
    touched.add(key);
    void check().then(() => {
      if (!report.autorun || !ready.peek()) return;
      clearTimeout(autoTimer);
      autoTimer = setTimeout(() => void run(payload()), 250);
    });
  };

  // The address follows the filters the report ran with, the sort and the page.
  const addressOf = (filters: Record<string, unknown>, order: Sort) => {
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(filters)) if (!blank(value)) params[key] = toAddress(value);
    if (!Object.keys(params).length) params.run = '1';
    if (order) params.sort = `${order.dir === 'desc' ? '-' : ''}${order.key}`;
    return params;
  };
  effect(() => {
    const filters = executed.get();
    if (!filters) return;
    const params = addressOf(filters, sort.get());
    if (page.get() > 1) params.page = String(page.get());
    const href = router.href('report', { report: name }, params);
    untrack(() => { const now = router.route.peek(); if (now?.name === 'report' && now.params.report === name && href.endsWith(now.url)) return; void router.navigate(href, { replace: true }); });
  });

  // ---------- header ----------

  const designs = ctx.schema.schema.printing?.available ? report.prints ?? [] : [];
  const share = () => void copyText(window.location.origin + (router.route.peek()?.url ?? router.href('report', { report: name })), 'the link to this view');
  const exportCsv = async () => {
    try {
      const file = await api.reportFile(name, 'export.csv', { filters: executed.peek() ?? {}, sort: sortText() });
      save(file, `${report.label}.csv`);
      toast(`Exported ${report.label}`, { icon: 'download' });
    } catch (error) { toast(error instanceof ApiError ? error.message : 'The export failed.', { icon: 'info' }); }
  };
  const header = PageHeader({ icon: REPORT_ICON, tint: 'rose', title: report.label, count: () => result.get()?.total ?? null, actions: [
    Button({ label: 'Copy link', icon: 'link', hideLabelOnPhone: true, disabled: () => !executed.get(), onClick: share }),
    report.export ? Button({ label: 'Export', icon: 'download', hideLabelOnPhone: true, disabled: () => !executed.get() || busy.get(), onClick: () => void exportCsv() }) : null,
    designs.length ? Button({ label: 'Print', icon: 'printer', hideLabelOnPhone: true, disabled: () => !executed.get() || busy.get(),
      onClick: () => openReportPrint(ctx, report, designs, executed.peek() ?? {}) }) : null,
  ].filter(Boolean) as Child[] });

  // ---------- filter bar ----------

  const shown = report.fields.filter(meta => !meta.hidden);
  const filterBar = shown.length ? html`<form class="ev-report-filters" aria-label=${`${report.label} filters`} on:submit=${(event: Event) => { event.preventDefault(); runNow(); }}>
    <div class="ev-report-fields">${shown.map(meta => fieldFor({ field: fields.get(meta.name)!, links: ctx.links, onCommit: commit(meta.name) }))}</div>
    ${report.autorun ? null : Button({ label: () => busy.get() ? 'Running…' : 'Run', variant: 'primary', type: 'submit', disabled: () => busy.get() || checking.get() && !ready.get() })}
    </form>` : null;
  const status = html`<div class="ev-report-status">
    <p class="ev-error" role="alert" hidden=${() => !note.get()}>${note}</p>
    <p class="ev-report-changed" role="status" hidden=${() => !changed.get() || report.autorun}>Filters changed. Run again to update these results.</p></div>`;

  // The filters a run used, as chips under the bar.
  const applied = () => {
    const filters = executed.get();
    if (!filters) return null;
    const chips = shown.filter(meta => !blank(filters[meta.name])).map(meta => {
      const value = filters[meta.name];
      const text = meta.type === 'link' && meta.target ? () => ctx.links.title(meta.target!, String(value)).get() ?? String(value)
        : meta.type === 'date' ? formatDate(String(value)) : meta.type === 'boolean' ? (value ? 'Yes' : 'No')
          : meta.type === 'select' ? humanize(String(value)) : Array.isArray(value) ? value.map(item => humanize(String(item))).join(', ') : String(value);
      return html`<span class="chip ev-report-chip"><span class="muted">${meta.label}</span> <b>${text}</b></span>`;
    });
    return chips.length ? chips : html`<span class="muted">All records</span>`;
  };

  // ---------- saved views ----------

  const views = ctx.prefs.cell<SavedView[]>(`report.${name}.views`, []);
  const applyView = (view: SavedView) => {
    batch(() => {
      for (const [key, field] of fields) {
        if (field.calculated || field.meta.read_only) continue;
        const given = view.query[key];
        field.value.set(given !== undefined ? fromAddress(field.meta, given) : initial(field.meta));
        touched.add(key);
      }
      quiet = true;
      sort.set(view.query.sort && report.sortable.includes(view.query.sort.replace(/^-/, '')) ? sortFrom(view.query.sort) : null);
      quiet = false;
    });
    runNow();
  };
  const saveView = () => {
    const filters = executed.peek();
    if (!filters) return;
    const label = localField({ name: 'view_name', type: 'text', label: 'Name', required: true }, '');
    const keep = (close: () => void) => {
      const text = String(label.value.peek() ?? '').trim().slice(0, 60);
      if (!text) { label.error.set('Give the view a name'); return; }
      const query = addressOf(filters, sort.peek());
      delete query.run;
      views.set([...views.peek().filter(view => view.name !== text), { name: text, query }].slice(-MAX_VIEWS));
      close();
      toast(`Saved the view ${text}`, { icon: 'check' });
    };
    const close = openModal({ title: 'Save this view', icon: REPORT_ICON, tint: 'rose', narrow: true, onSubmit: () => keep(close),
      body: () => [fieldFor({ field: label }), html`<p class="muted">It keeps the filters this report ran with and its sort, for you on this device.</p>`],
      footer: done => DialogFooter({ cancel: done, submit: { label: 'Save view', onClick: () => keep(done) } }) });
  };
  const viewBar = html`<div class="ev-report-views" role="group" aria-label="Saved views">
    ${() => views.get().map(view => html`<span class="ev-report-view"><button type="button" class="chip" on:click=${() => applyView(view)}>${view.name}</button>
      ${IconButton({ icon: 'x', tip: `Remove ${view.name}`, onClick: () => views.set(views.peek().filter(item => item !== view)) })}</span>`)}
    ${Button({ label: 'Save view', icon: 'plus', variant: 'ghost', size: 'small', disabled: () => !executed.get(), onClick: saveView })}</div>`;

  // ---------- chart ----------

  const chartOn = ctx.prefs.cell<boolean>(`report.${name}.chart`, true);
  const chart = report.chart ? when(() => chartOn.get() && dataRows.get().length > 0, () => ReportChart(ctx, report, () => dataRows.get())) : null;

  // ---------- table ----------

  const tablePrefs = ctx.prefs.cell<TablePrefs>(`report.${name}`, emptyPrefs());
  const columns: Column<Row>[] = report.columns.map((column, index) => ({
    key: column.name, label: columnLabel(column), align: NUMBERS.includes(column.type) ? 'right' : 'left',
    width: index === 0 ? 220 : column.type === 'currency' ? 150 : column.type === 'boolean' ? 100 : 160,
    sortable: report.sortable.includes(column.name), value: (row: Row) => row[column.name],
    render: (row: () => Row) => {
      const value = () => row()[column.name];
      // A group heading names the group and its row count; a subtotal shows the group's totals.
      if (row()._kind === 'head') return column.name === grouping
        ? html`<span class="ev-report-group-name">${() => cellText(ctx, column, value()) || 'Not set'}<small class="muted">${() => { const count = row()._group?.count; return count === undefined ? '' : ` · ${count} ${count === 1 ? 'row' : 'rows'}`; }}</small></span>` : null;
      if (row()._kind === 'sub') return column.name === grouping ? html`<span class="muted">Subtotal</span>`
        : column.total ? html`<span class="num">${() => { const total = row()._group?.totals[column.name]; return total === undefined || total === null ? '' : cellText(ctx, column, total); }}</span>` : null;
      if (column.type === 'link' && column.link) return when(() => !blank(value()), () =>
        html`<a class="ev-row-link" href=${() => router.href('record', { model: column.link!, id: String(value()) })}>${() => cellText(ctx, column, value())}</a>`);
      return html`<span class=${NUMBERS.includes(column.type) ? 'num' : null}>${() => cellText(ctx, column, value())}</span>`;
    },
  }));
  const totals = Object.fromEntries(report.columns.filter(column => column.total).map(column =>
    [column.name, () => { const value = result.get()?.totals?.values[column.name]; return value === undefined ? '' : cellText(ctx, column, value); }]));
  const table = () => DataTable<Row>({ columns, rows, key: row => row._row, label: report.label, pinned: 1, sort, serverSort: true, prefs: tablePrefs, height: 560,
    rowClass: row => row._kind ? `ev-report-${row._kind}` : null,
    totals: Object.keys(totals).length ? totals : undefined,
    totalsLabel: () => result.get()?.totals?.scope === 'page' && (result.get()?.total ?? 0) > dataRows.get().length ? 'Total, this page' : 'Total',
    empty: () => EmptyState({ icon: 'search', tint: 'slate', title: 'No rows match', text: 'Try different filters.' }) });

  // The same Columns menu as a record's lines: order, show or hide, and pin columns on the left.
  const chooser = (anchor: HTMLElement) => openPopover(anchor, () => html`<div class="ev-columns">${ColumnsMenu({ columns, prefs: tablePrefs, pinned: 1,
    widthOf: key => tablePrefs.peek().widths[key] ?? columns.find(column => column.key === key)?.width ?? 160,
    viewWidth: () => anchor.closest('.page')?.querySelector<HTMLElement>('.table-wrap')?.clientWidth ?? 0,
    note: 'Kept for you on this device. Drag a grip, or a header, to move a column.',
    extra: report.chart ? html`<hr>${Checkbox({ label: 'Chart', checked: chartOn, onChange: on => chartOn.set(on) })}` : null })}</div>`,
  { label: 'Columns', role: 'dialog', className: 'cols-pop' });

  const pages = derive(() => Math.max(1, Math.ceil((result.get()?.total ?? 0) / PAGE)));
  const pager = html`<nav class="ev-pager" aria-label="Pages" hidden=${() => (result.get()?.total ?? 0) <= PAGE}>
    <span class="num">${() => { const from = (page.get() - 1) * PAGE + 1; return `${from}–${Math.min(result.get()?.total ?? 0, from + PAGE - 1)} of ${result.get()?.total ?? 0}`; }}</span>
    ${IconButton({ icon: 'chevron-left', tip: 'Previous page', disabled: () => busy.get() || page.get() <= 1, onClick: () => again(page.peek() - 1) })}
    ${IconButton({ icon: 'chevron-right', tip: 'Next page', disabled: () => busy.get() || page.get() >= pages.get(), onClick: () => again(page.peek() + 1) })}</nav>`;

  const toolbar = Toolbar({ label: `${report.label} tools`, left: [html`<div class="ev-report-applied" aria-label="Filters this report ran with">${() => applied()}</div>`],
    right: [html`<span class="summary" aria-live="polite">${() => busy.get() ? 'Running…' : result.get() ? `${result.get()!.total} ${result.get()!.total === 1 ? 'row' : 'rows'}` : ''}</span>`,
      Button({ label: 'Columns', icon: 'columns-3', variant: 'ghost', hideLabelOnPhone: true, onClick: event => chooser(event.currentTarget as HTMLElement) })] });

  const mode = derive(() => failure.get() ? 'error' : result.get() ? 'rows' : 'waiting');
  const content = match(mode, {
    error: () => EmptyState({ icon: 'info', tint: 'rose', title: 'The report could not be run', text: () => failure.get() ?? '', action: Button({ label: 'Try again', onClick: runNow }) }),
    waiting: () => EmptyState({ icon: REPORT_ICON, tint: 'rose', title: () => busy.get() ? 'Running…' : report.autorun ? 'Fill in the filters' : 'Set the filters, then run',
      text: report.autorun ? 'The report runs as soon as the filters are complete.' : 'Nothing runs until you choose Run.' }),
    rows: () => [chart, table(), pager],
  });

  return html`<div class="page ev-report" data-report=${name} aria-busy=${() => busy.get() ? 'true' : null}>${header}${viewBar}${filterBar}${status}${toolbar}${content}</div>` as HTMLElement;
}

/** Hand a file to the browser to save. */
function save(file: Blob, name: string) {
  const url = URL.createObjectURL(file);
  saveUrl(url, name);
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ---------- chart ----------

/** Bars or lines of the shown rows: one label column and up to four number columns, drawn to one scale. */
function ReportChart(ctx: AppContext, report: ReportSchema, rows: () => Row[]): HTMLElement {
  const spec = report.chart!;
  const xColumn = report.columns.find(column => column.name === spec.x)!;
  const series = spec.y.map(key => report.columns.find(column => column.name === key)!).filter(Boolean);
  const W = 720, H = 220, L = 56, R = 12, T = 16, B = 30;
  const draw = () => {
    const list = rows().slice(0, 40);
    const values = list.flatMap(row => series.map(column => Number(row[column.name]) || 0));
    const top = niceTop(Math.max(0, ...values)), bottom = Math.min(0, ...values);
    const span = top - bottom || 1;
    const y = (value: number) => T + (top - value) / span * (H - T - B);
    const band = (W - L - R) / Math.max(1, list.length);
    const label = (row: Row) => cellText(ctx, xColumn, row[xColumn.name]);
    const tip = (row: Row, column: ReportColumn) => svg('title', {}, [`${label(row)}: ${columnLabel(column)} ${cellText(ctx, column, row[column.name])}`]);
    // Axis values are fractions of the scale: formatNumber takes them as fixed decimal text.
    const shortNumber = (value: number) => Math.abs(value) >= 1e6 ? `${formatNumber((value / 1e6).toFixed(1), 1)}M` : Math.abs(value) >= 1e3 ? `${formatNumber((value / 1e3).toFixed(1), 1)}k` : formatNumber(value.toFixed(0), 0);
    const parts: SVGElement[] = [];
    for (const value of [0, 0.25, 0.5, 0.75, 1].map(step => bottom + span * step)) {
      parts.push(svg('line', { class: 'grid', x1: L, x2: W - R, y1: y(value), y2: y(value) }),
        svg('text', { class: 'tick', x: L - 8, y: y(value) + 4, 'text-anchor': 'end' }, [shortNumber(value)]));
    }
    series.forEach((column, s) => {
      const at = (row: Row) => y(Number(row[column.name]) || 0);
      if (spec.type === 'line') {
        parts.push(svg('polyline', { class: `s${s}`, fill: 'none', 'stroke-width': 2, points: list.map((row, i) => `${L + band * (i + 0.5)},${at(row)}`).join(' ') }));
        list.forEach((row, i) => parts.push(svg('circle', { class: `s${s}`, r: 3, cx: L + band * (i + 0.5), cy: at(row) }, [tip(row, column)])));
        return;
      }
      const width = band * 0.7 / series.length;
      list.forEach((row, i) => parts.push(svg('rect', { class: `s${s}`, rx: 3, x: L + band * (i + 0.15) + width * s, width: Math.max(1, width - 2),
        y: Math.min(at(row), y(0)), height: Math.abs(at(row) - y(0)) }, [tip(row, column)])));
    });
    const every = Math.ceil(list.length / 12);
    list.forEach((row, i) => { if (!(i % every)) parts.push(svg('text', { class: 'tick', x: L + band * (i + 0.5), y: H - 10, 'text-anchor': 'middle' }, [clip(label(row), 14)])); });
    return svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${spec.label || report.label} chart` }, parts);
  };
  const host = html`<div class="ev-report-chart-plot"></div>` as HTMLElement;
  effect(() => { host.replaceChildren(draw()); });
  return html`<section class="ev-report-chart" aria-label="Chart"><div class="ev-report-chart-h"><h3>${spec.label || series.map(columnLabel).join(' and ')}</h3>
    <span class="ev-report-legend">${series.length > 1 ? series.map((column, s) => html`<span><i class=${`sw s${s}`}></i>${columnLabel(column)}</span>`) : null}</span>
    <span class="muted">${() => rows().length > 40 ? 'First 40 rows of this page' : 'Rows on this page'}</span></div>${host}</section>` as HTMLElement;
}

/** An SVG element; text children become text nodes, so nothing is parsed from markup. */
function svg(tag: string, attributes: Record<string, string | number>, children: Array<SVGElement | string> = []): SVGElement {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  element.append(...children);
  return element;
}

function niceTop(value: number) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map(step => step * power).find(step => step >= value)!;
}
const clip = (text: string, size: number) => text.length > size ? `${text.slice(0, size - 1)}…` : text;

// ---------- print ----------

/** The print preview of a report: the PDF Print Design renders for this person from the filters it ran with. */
export function openReportPrint(ctx: AppContext, report: ReportSchema, designs: NonNullable<ReportSchema['prints']>, filters: Record<string, unknown>) {
  const design = cell((designs.find(item => item.default) ?? designs[0]).name);
  const pdf = cell<string | null>(null), problem = cell<string | null>(null);
  let current: string | null = null;
  const release = () => { if (current) URL.revokeObjectURL(current); current = null; };
  const load = async (name: string) => {
    batch(() => { pdf.set(null); problem.set(null); });
    try {
      const file = await ctx.api.reportFile(report.name, { print: name }, { filters });
      if (design.peek() !== name) return;
      release();
      current = URL.createObjectURL(file);
      pdf.set(current);
    } catch (error) { if (design.peek() === name) problem.set(error instanceof ApiError ? error.message : 'The PDF could not be made.'); }
  };
  void load(design.peek());
  const title = designs.find(item => item.name === design.peek())?.title ?? report.label;
  openDrawer({ title: `Print ${report.label}`, icon: 'printer', className: 'ev-print', onClose: release,
    body: () => html`<div class="ev-print-body">
      ${designs.length > 1 ? html`<label class="ev-print-design"><span class="muted">Print design</span>
        <select class="input" aria-label="Print design" on:change=${(event: Event) => { const next = (event.target as HTMLSelectElement).value; design.set(next); void load(next); }}>
          ${designs.map(item => html`<option value=${item.name} selected=${item.name === design.peek()}>${item.title}</option>`)}</select></label>` : null}
      ${() => problem.get() ? html`<p class="ev-error" role="alert">${problem.get()}</p>`
        // The PDF is a blob: address this page made from the server's answer; templates only take web addresses, so it is set here.
        : pdf.get() ? html`<iframe class="ev-print-frame" title=${`${title}, PDF preview`} ref=${(frame: HTMLIFrameElement) => { frame.src = pdf.peek()!; }}></iframe>` : html`<p class="muted">Making the PDF…</p>`}
      <p class="muted ev-print-note">Printed as you, with the filters this report last ran with.</p></div>`,
    footer: close => html`<div class="ev-print-actions">
      ${Button({ label: 'Download', icon: 'download', variant: 'primary', disabled: () => !pdf.get(), onClick: () => { const url = pdf.peek(); if (url) saveUrl(url, `${report.label}.pdf`); } })}
      ${Button({ label: 'Close', onClick: close })}</div>` });
}

function saveUrl(url: string, name: string) {
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
}
