// The generated list page of a model (docs/ui-design/README.md section 6, List pages): page header,
// tabs by one field's values (Meta.list_tabs), toolbar with search and filters, the model's measures, a DataTable with
// the list fields (title pinned), sorting, paging and a bulk bar, or a board by State.
//
// Search, sort, page, view, tab and filters live in the address, so a link opens the same list.
// With no tab there, the list opens on the model's default tab (Meta.list_tab); choosing another,
// All included, puts it in the address. Everything runs on the server: the browser never filters or sorts a partial list.
// The filter builder (rows of field, condition and value, child tables included) is filters.ts.
//
// A record opens on its own page or in a side panel beside the narrowed list (Meta.open_in, the
// Controls list view, or the person's choice in the layout menu). The panel's record is `?open=`
// in the list's address and its tab `?rtab=`, so a link or a reload opens the same list and record.

import { each, html, match, when, type Child } from '../dom';
import { ApiError, type RecordData } from '../api';
import type { ListTabCounts } from './client';
import { isRecordId } from '../ids';
import { batch, cell, derive, effect, onCleanup, untrack } from '../reactive';
import { Button, CountBadge, EmptyState, IconButton, SearchInput, SegmentedControl, SummaryStrip, type StatProps } from '../ui/base';
import { currencyIn, displayValue, localField, stateTint, statusTint } from '../ui/fields';
import { formatMoney } from '../model/format';
import { humanize } from '../ui/kinds';
import { closeDialogs, DialogFooter, MenuButton, openModal, toast } from '../ui/layers';
import { SplitView } from '../ui/split';
import { Board, BoardCard, BulkBar, PageHeader, Toolbar, type BoardColumn, type BoardTotal } from '../ui/list';
import { StatusBadge, Tabs } from '../ui/doc';
import { DataTable, emptyPrefs, type Column, type Sort, type TablePrefs } from '../ui/table';
import type { FieldSchema } from '../model/schema';
import type { AppContext } from './context';
import { compact, filterBuilder, FilterChips, FilterPanel } from './filters';
import { REPORT_ICON, reportsFor } from './report-page';
import { fieldOf, formatOf, identity, isStateField, keyFields, listFields, measureLabel, measureText, LIST_KEYS, numberField, screenModel, sources, titleOf, type ScreenModel } from './meta';
import { RecordPage, type PanelHost } from './record-page';
import { recordId } from '../ids';

export const PAGE_SIZE = 50;
const BOARD_SIZE = 100;
/** Below this width the list beside a panel shows each record on two lines. */
const COMPACT_WIDTH = 460;

/** Bulk changes send at most this many requests at once. */
const BULK_AT_ONCE = 4;

/** A list cell: status badge for the State and Meta.status, record title for links, formatted value. */
export function cellView(ctx: AppContext, model: ScreenModel, meta: FieldSchema, row: () => RecordData): Child {
  if (isStateField(model, meta) || meta.name === model.status) return StatusBadge({
    value: () => { const value = row()[meta.name]; return value === null || value === undefined ? null : String(value); },
    tint: value => statusTint(meta, value), name: meta.label });
  if (meta.type === 'link' || meta.type === 'tags' || meta.type === 'boolean' || meta.type === 'state') {
    const field = localField(meta as any, untrack(() => row()[meta.name]), {});
    effect(() => field.value.set(row()[meta.name]));
    return displayValue(field, { links: ctx.links, empty: '' });
  }
  // A Currency field whose code comes from the record (or a record it links to) shows that code.
  if (meta.type === 'currency' && meta.currency_source) return () => {
    const value = row()[meta.name];
    return value === null || value === undefined || value === '' ? ''
      : formatMoney(String(value), { currency: currencyIn(meta, name => row()[name], ctx.links), scale: typeof meta.scale === 'number' ? meta.scale : 2 });
  };
  return () => formatOf(meta, row()[meta.name]);
}

export function ListPage(ctx: AppContext, name: string): HTMLElement {
  const { api, router } = ctx;
  const model = screenModel(ctx.schema, name);
  const { icon, tint } = identity(model);
  const state = model.state && fieldOf(model, model.state.field) ? model.state : null;
  const canCreate = model.api !== 'read' && model.permissions?.create !== false;
  const canDelete = model.api !== 'read' && model.permissions?.delete === true;
  const fields = listFields(model);
  const start = router.route.peek()?.query ?? {};
  ctx.crumbs.set([{ label: model.plural }]);

  // ---------- how records open ----------

  const openChoice = ctx.prefs.cell<'page' | 'panel' | null>(`openIn.${name}`, null);
  const openIn = derive(() => openChoice.get() ?? model.open_in ?? 'page', { name: `${name}.open in` });
  /** The record open in the side panel (its id, or 'new'), from the address. */
  const opened = derive(() => {
    const route = router.route.get();
    return route?.name === 'list' && route.params.model === name ? route.query.open ?? null : null;
  }, { name: `${name}.open record` });
  const panelOpen = derive(() => opened.get() !== null);
  /** The list's address with this record (and record tab) in the panel, or none. */
  const listHref = (open: string | null, rtab: string | null = null) => {
    const route = router.route.peek();
    const mine = route?.name === 'list' && route.params.model === name ? route.query : {};
    const query = Object.fromEntries(Object.entries(mine).filter(([key]) => LIST_KEYS.includes(key) && key !== 'open' && key !== 'rtab'));
    if (open) query.open = open;
    if (open && rtab) query.rtab = rtab;
    return router.href('list', { model: name }, query);
  };

  // ---------- the list's state, from and to the address ----------

  const q = cell(start.q ?? '', { name: `${name}.q` });
  const sortFromUrl = (value: string | undefined): Sort => !value ? null : value.startsWith('-') ? { key: value.slice(1), dir: 'desc' } : { key: value, dir: 'asc' };
  const sort = cell<Sort>(sortFromUrl(start.sort ?? model.order_by), { name: `${name}.sort` });
  // Rows per page from a Controls list view, else the framework default.
  const size = model.page_size ?? PAGE_SIZE;
  const page = cell(Math.max(1, Number(start.page) || 1), { name: `${name}.page` });
  const view = cell<'list' | 'board'>(state && start.view === 'board' ? 'board' : 'list', { name: `${name}.view` });
  // ---------- list tabs: a State, Select or Link field's values (Meta.list_tabs) ----------
  const tabSpec = model.list_tabs && fieldOf(model, model.list_tabs.field) ? model.list_tabs : null;
  const tabMeta = tabSpec ? fieldOf(model, tabSpec.field)! : null;
  const linkTabs = tabSpec?.kind === 'link' ? tabMeta!.target ?? null : null;
  /** State and Select tabs: every value but retired choices. Link tabs come from the counts. */
  const fixedTabs = !tabSpec || tabSpec.kind === 'link' ? [] : state && tabSpec.field === state.field ? state.states
    : (tabMeta!.choices ?? []).filter(value => !(tabMeta!.retired_choices ?? []).includes(value));
  const fits = (value: string | undefined): value is string => Boolean(tabSpec && value
    && (value === 'all' || (tabSpec.kind === 'link' ? Boolean(linkTabs) && isRecordId(value) : fixedTabs.includes(value))));
  /** The tab the list opens on. A Link's default names a record by its title, found once the page loads. */
  const home = cell(tabSpec && tabSpec.kind !== 'link' && fits(tabSpec.default) ? tabSpec.default : 'all', { name: `${name}.home tab` });
  const finding = cell(Boolean(linkTabs && tabSpec!.default !== 'all' && !fits(start.tab)), { name: `${name}.finding home tab` });
  const tab = cell(fits(start.tab) ? start.tab : home.peek(), { name: `${name}.tab` });
  if (finding.peek()) void findRecord(ctx, linkTabs!, tabSpec!.default).then(id => batch(() => {
    if (id) { home.set(id); tab.set(id); }
    finding.set(false);
  }));
  const filters = filterBuilder(ctx.schema, model, start.f);
  const filtering = cell(false, { name: `${name}.filter panel` });
  const selection = cell(new Set<string | number>(), { name: `${name}.selection` });
  const query = cell(q.peek(), { name: `${name}.q (debounced)` });
  let typing: ReturnType<typeof setTimeout> | undefined;
  changes(() => q.get(), () => { clearTimeout(typing); typing = setTimeout(() => batch(() => { query.set(q.peek()); page.set(1); }), 250); });
  onCleanup(() => clearTimeout(typing));

  // The saved view's State is one more condition. Only equalities on distinct fields go as the shorter object form.
  const serverFilters = derive(() => compact([...filters.server.get(), ...(tabSpec && tab.get() !== 'all' ? [[tabSpec.field, 'eq', tab.get()]] : [])]));
  changes(() => filters.server.get(), () => page.set(1));
  effect(() => {
    const params: Record<string, string> = {};
    if (query.get()) params.q = query.get();
    const order = sort.get();
    if (order) params.sort = `${order.dir === 'desc' ? '-' : ''}${order.key}`;
    if (page.get() > 1) params.page = String(page.get());
    if (view.get() === 'board') params.view = 'board';
    if (tabSpec && tab.get() !== home.get()) params.tab = tab.get();
    if (filters.address.get()) params.f = filters.address.get();
    // The panel's record stays open while the list changes around it.
    const now = untrack(() => router.route.peek()?.query ?? {});
    if (now.open) params.open = now.open;
    if (now.open && now.rtab) params.rtab = now.rtab;
    // A create link (?open=new&customer=4) keeps its values while the new record is open.
    if (now.open === 'new') for (const [key, value] of Object.entries(now)) if (!LIST_KEYS.includes(key)) params[key] = value;
    const href = router.href('list', { model: name }, params);
    untrack(() => { if (router.route.peek()?.name === 'list' && router.route.peek()?.params.model === name && href.endsWith(router.route.peek()!.url)) return; void router.navigate(href, { replace: true }); });
  });

  // ---------- data ----------

  const rows = cell<RecordData[]>([], { name: `${name}.rows` });
  const total = cell<number | null>(null, { name: `${name}.total` });
  const loading = cell(true), failure = cell<string | null>(null);
  const reload = cell(0);
  let ticket = 0;
  effect(() => {
    // A Link's default tab is looked up first, so the list loads once.
    if (finding.get()) return;
    const board = view.get() === 'board';
    const request = { q: query.get() || undefined, filters: serverFilters.get(), limit: board ? BOARD_SIZE : size,
      offset: board ? 0 : (page.get() - 1) * size, orderBy: board ? undefined : sort.get() ? `${sort.get()!.dir === 'desc' ? '-' : ''}${sort.get()!.key}` : undefined };
    reload.get();
    const mine = ++ticket;
    loading.set(true);
    void api.list(name, request).then(result => {
      if (mine !== ticket) return;
      batch(() => { rows.set(result.items); total.set(result.total); failure.set(null); loading.set(false); selection.set(new Set()); });
      // A page past the end (after deletes or a narrower filter) goes back to the last page.
      if (!board && !result.items.length && result.total > 0 && page.peek() > 1) page.set(Math.ceil(result.total / size));
    }, (error: unknown) => {
      if (mine !== ticket || (error instanceof ApiError && error.kind === 'aborted')) return;
      batch(() => { failure.set(error instanceof ApiError ? error.message : 'The list could not be loaded.'); loading.set(false); });
    });
  });
  const refresh = () => reload.set(reload.peek() + 1);

  // Model-level measures (Phase 9) follow the list's filters and its search box.
  const declared = (model.measures ?? []).filter(measure => !measure.per_record);
  const measures = cell<Record<string, unknown> | null>(null);
  if (declared.length) effect(() => {
    if (finding.get()) return;
    const current = serverFilters.get(), text = query.get();
    reload.get();
    void api.measures(name, current, text).then(values => measures.set(values), () => measures.set(null));
  });
  // Board column totals by state, from one aggregate (Phase 9): a count and the board's declared
  // sum measure, with the measure's own where. The aggregate route works without declared
  // measures and follows the search box too.
  const sumMeasure = declared.find(measure => measure.function === 'sum' && measure.field && !measure.where) ?? declared.find(measure => measure.function === 'sum' && measure.field);
  const valueField = sumMeasure?.field ? fieldOf(model, sumMeasure.field) : undefined;
  const countName = sumMeasure?.name === 'n' ? 'records' : 'n';
  const grouped = cell<Array<Record<string, unknown>> | null>(null);
  const totalsTick = cell(0);
  let aggregateTicket = 0;
  if (state) effect(() => {
    if (view.get() !== 'board') return;
    // The totals per state follow every filter but the tab.
    const others = compact(filters.server.get());
    const text = query.get();
    reload.get();
    totalsTick.get();
    const mine = ++aggregateTicket;
    void api.aggregate(name, [`${countName}:count`, ...(sumMeasure ? [sumMeasure.name] : [])], [state.field], others, text)
      .then(result => { if (mine === aggregateTicket) grouped.set(result.items); }, () => { if (mine === aggregateTicket) grouped.set(null); });
  });
  // Tab counts follow every filter but the tab, and the search box.
  const tabCounts = cell<ListTabCounts | null>(null, { name: `${name}.tab counts` });
  let countsTicket = 0;
  if (tabSpec) effect(() => {
    if (finding.get()) return;
    const others = compact(filters.server.get());
    const text = query.get();
    reload.get();
    totalsTick.get();
    const mine = ++countsTicket;
    void api.listTabs(name, others, text).then(result => { if (mine === countsTicket) tabCounts.set(result); }, () => { if (mine === countsTicket) tabCounts.set(null); });
  });
  const counts = derive(() => { const result = tabCounts.get(); return result ? Object.fromEntries(result.items.map(item => [item.value, item.count])) : null; });
  const boardTotals = derive<Record<string, BoardTotal> | null>(() => {
    const items = grouped.get();
    if (!items || !state) return null;
    const amountOf = (item: Record<string, unknown> | undefined) => sumMeasure ? item?.[sumMeasure.name] ?? '0' : null;
    const all = items.reduce((sum, item) => sum + (Number(amountOf(item)) || 0), 0);
    return Object.fromEntries(state.states.map(value => {
      const item = items.find(entry => String(entry[state.field]) === value);
      const amount = amountOf(item);
      return [value, { count: Number(item?.[countName] ?? 0), amount: valueField && amount !== null ? formatOf(valueField, amount) : undefined,
        share: all ? (Number(amount) || 0) / all * 100 : 0 }];
    }));
  });

  // ---------- header, views and toolbar ----------

  const create = () => openIn.peek() === 'panel' ? void router.navigate(listHref('new'), { replace: panelOpen.peek() }) : void router.go('create', { model: name });
  if (canCreate) ctx.createHere.set(create);
  onCleanup(() => { if (ctx.createHere.peek() === create) ctx.createHere.set(null); });

  const filterButton = Button({ label: 'Filter', icon: 'sliders-horizontal', hideLabelOnPhone: true, onClick: () => filtering.set(!filtering.peek()) });
  filterButton.setAttribute('aria-controls', `ev-${name}-filters`);
  effect(() => filterButton.setAttribute('aria-expanded', String(filtering.get())));
  filterButton.append(CountBadge({ count: () => filters.active.get().length, tone: 'filled', label: 'Active filters' }));
  effect(() => { filterButton.classList.toggle('has-filters', filters.active.get().length > 0); });

  const header = PageHeader({ icon, tint, title: model.plural, count: () => total.get(), actions: [
    state ? SegmentedControl({ options: [{ value: 'list', label: 'List', icon: 'list' }, { value: 'board', label: 'Board', icon: 'kanban' }], value: view, label: 'View',
      onChange: next => batch(() => { view.set(next); selection.set(new Set()); }) }) : null,
    filters.fields.length ? filterButton : null,
    // Reports declared for this model (Report.model) open from here.
    reportsFor(ctx, name).length ? MenuButton({ label: 'Reports', icon: REPORT_ICON, hideLabelOnPhone: true, menuLabel: `${model.label} reports`,
      items: reportsFor(ctx, name).map(report => ({ label: report.label, icon: REPORT_ICON, tint: 'rose' as const, onSelect: () => void router.go('report', { report: report.name }) })) }) : null,
    MenuButton({ icon: 'panel-right', tip: 'Open records in', ariaLabel: 'Open records in', menuLabel: 'Open records in', items: () => [{ heading: 'Open records in' },
      { label: 'Side panel', icon: openIn.peek() === 'panel' ? 'check' : 'panel-right', onSelect: () => openChoice.set('panel') },
      { label: 'Full page', icon: openIn.peek() === 'page' ? 'check' : 'maximize-2', onSelect: () => openChoice.set('page') }] }),
    canCreate ? Button({ label: `New ${model.label.toLowerCase()}`, icon: 'plus', variant: 'primary', hideLabelOnPhone: true, onClick: create }) : null,
  ].filter(Boolean) as Child[] });

  // A Link's tabs are its most used values (and the open one), so they are drawn again when those change.
  const tabValues = derive(() => {
    if (!linkTabs) return fixedTabs.join('\n');
    const values = (tabCounts.get()?.items ?? []).map(item => item.value);
    for (const value of [home.get(), tab.get()]) if (value !== 'all' && !values.includes(value)) values.push(value);
    return values.join('\n');
  }, { name: `${name}.tab values` });
  const tabCount = (value: string) => () => tabCounts.get() ? value === 'all' ? tabCounts.get()!.total : counts.get()![value] ?? 0 : tab.get() === value ? total.get() : null;
  const tabLabel = (value: string) => linkTabs ? () => ctx.links.title(linkTabs, value).get() ?? '…' : humanize(value);
  const tabs = tabSpec ? () => {
    if (finding.get()) return null;
    const values = tabValues.get() ? tabValues.get().split('\n') : [];
    return untrack(() => Tabs({ label: `${model.plural} by ${tabMeta!.label.toLowerCase()}`, active: tab, idPrefix: `ev-${name}-views`, tabs: [
      { id: 'all', label: `All ${model.plural.toLowerCase()}`, count: tabCount('all') },
      ...values.map(value => ({ id: value, label: tabLabel(value), count: tabCount(value) })),
    ] }));
  } : null;
  changes(() => tab.get(), () => batch(() => { page.set(1); selection.set(new Set()); }));

  const search = (model.search_fields ?? []).length ? SearchInput({ value: q, placeholder: `Search ${model.plural.toLowerCase()}`, label: `Search ${model.plural.toLowerCase()}` }) : null;
  if (search) {
    ctx.pageSearch.set(search.querySelector('input'));
    onCleanup(() => { if (ctx.pageSearch.peek()?.isConnected === false || ctx.pageSearch.peek() === search.querySelector('input')) ctx.pageSearch.set(null); });
  }
  // Clearing goes back to the tab the list opens on.
  const clearAll = () => batch(() => { filters.clear(); q.set(''); tab.set(home.peek()); page.set(1); });
  const toolbar = Toolbar({ label: `${model.plural} tools`, left: [search, FilterChips(filters, ctx.links)],
    right: [html`<span class="summary" aria-live="polite">${() => loading.get() ? 'Loading…' : total.get() === null ? '' : `${total.get()} ${total.get() === 1 ? model.label.toLowerCase() : model.plural.toLowerCase()}`}</span>`] });

  const strip = declared.length ? when(() => measures.get() !== null, () => SummaryStrip(declared.slice(0, 4).map(measure => ({
    label: measureLabel(measure), value: () => measureText(ctx.schema, model, measure, measures.get()?.[measure.name]),
    note: measure.where ? 'Filtered by a rule' : undefined,
  }) as StatProps))) : null;

  // ---------- table ----------

  let focusPanel = false;
  /** Open a record: in the panel (switching rows replaces the history entry, so Back closes it) or on its page. */
  const open = (row: RecordData, focus = true) => {
    if (openIn.peek() !== 'panel') return void router.go('record', { model: name, id: String(row.id) });
    if (opened.peek() === String(row.id)) return;
    focusPanel = focus;
    void router.navigate(listHref(String(row.id), router.route.peek()?.query.rtab ?? null), { replace: panelOpen.peek() });
  };
  const plainClick = (event: MouseEvent) => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  const numbered = numberField(model);
  const showNumber = numbered && numbered.name !== model.title_field && fields.some(meta => meta.name === numbered.name) ? numbered : null;
  const columns: Column<RecordData>[] = fields.map((meta, index) => ({
    key: meta.name, label: meta.label, align: ['currency', 'decimal', 'integer', 'percent', 'duration'].includes(meta.type) ? 'right' : 'left',
    width: index === 0 ? 240 : meta.type === 'currency' ? 140 : 160, value: (row: RecordData) => row[meta.name],
    sortable: meta.stored !== false && meta.type !== 'tags' && meta.type !== 'longtext' && meta.type !== 'richtext',
    render: index === 0 && meta.name === model.title_field
      ? (row: () => RecordData) => html`<a class="ev-row-link" href=${() => router.href('record', { model: name, id: String(row().id) })}
          on:click=${(event: MouseEvent) => { if (openIn.peek() === 'panel' && plainClick(event)) { event.preventDefault(); open(row()); } }}>${() => titleOf(model, row())}</a>${showNumber
          ? when(panelOpen, () => html`<small class="ev-row-no mono">${() => String(row()[showNumber.name] ?? '')}</small>`) : null}`
      : (row: () => RecordData) => cellView(ctx, model, meta, row),
  }));
  const onlyTab = derive(() => tab.get() !== 'all' && !query.get() && !filters.active.get().length);
  const tablePrefs = ctx.prefs.cell<TablePrefs>(`list.${name}`, emptyPrefs());
  const selectable = canDelete || Boolean(state?.moves.length);
  // Beside a panel the list keeps the title (with the number under it), the State and the first amount.
  const amountField = fields.find(meta => meta.type === 'currency') ?? fields.find(meta => ['decimal', 'integer'].includes(meta.type));
  const kept = new Set([fields[0]?.name, state?.field, amountField?.name]);
  const panelColumns = columns.filter(item => kept.has(item.key));
  const current = derive(() => { const id = opened.get(); return id === null ? null : rows.get().find(row => String(row.id) === id)?.id ?? null; });
  const table = (narrow = false) => DataTable<RecordData>({ columns: narrow ? panelColumns : columns, rows, key: row => row.id, label: model.plural, pinned: 1,
    selectable: selectable && !narrow, selection, sort, serverSort: true, onOpen: row => open(row), prefs: tablePrefs, height: 640,
    current: narrow ? current : undefined, onMove: narrow ? row => open(row, false) : undefined,
    // Empty only because of the tab: Clear filters would change nothing, so it offers All instead.
    empty: () => () => onlyTab.get()
      ? EmptyState({ icon: 'search', tint: 'slate', title: `No ${model.plural.toLowerCase()} in this tab`, text: 'Choose another tab, or show them all.',
        action: Button({ label: `Show all ${model.plural.toLowerCase()}`, onClick: () => tab.set('all') }) })
      : EmptyState({ icon: 'search', tint: 'slate', title: `No ${model.plural.toLowerCase()} match`, text: 'Try a different search or clear the filters.',
        action: Button({ label: 'Clear filters', onClick: clearAll }) }) });
  changes(() => sort.get(), () => page.set(1));

  const pages = derive(() => Math.max(1, Math.ceil((total.get() ?? 0) / size)));
  const pager = html`<nav class="ev-pager" aria-label="Pages" hidden=${() => (total.get() ?? 0) <= size}>
    <span class="num">${() => { const from = (page.get() - 1) * size + 1; return `${from}–${Math.min(total.get() ?? 0, from + size - 1)} of ${total.get() ?? 0}`; }}</span>
    ${IconButton({ icon: 'chevron-left', tip: 'Previous page', disabled: () => page.get() <= 1, onClick: () => page.set(Math.max(1, page.peek() - 1)) })}
    ${IconButton({ icon: 'chevron-right', tip: 'Next page', disabled: () => page.get() >= pages.get(), onClick: () => page.set(Math.min(pages.peek(), page.peek() + 1)) })}</nav>`;

  // ---------- board ----------

  const keys = keyFields(model);
  const linkField = keys.find(meta => meta.type === 'link');
  const dateField = keys.find(meta => meta.type === 'date') ?? model.fields.find(meta => meta.type === 'date');
  const role = ctx.user.role;
  const moveBlocked = (row: RecordData, to: string): string | null => {
    const from = String(row[state!.field]);
    const move = state!.moves.find(item => item.to === to && sources(item).includes(from));
    if (!move) return `${titleOf(model, row)} cannot move from ${humanize(from)} to ${humanize(to)}`;
    if (move.roles && !move.roles.includes(role)) return `Your role cannot use ${move.label}`;
    if (model.api === 'read' || model.permissions?.update === false) return 'You cannot change this record';
    // A move that asks for values (a reason, say) is made from the record, where its dialog asks for them.
    if (move.asks?.length) return `Open ${titleOf(model, row)} to use ${move.label}: it asks for ${move.asks.map(field => fieldOf(model, field)?.label.toLowerCase() ?? field).join(' and ')}`;
    return null;
  };
  const setRow = (id: string, next: RecordData) => rows.set(rows.peek().map(item => item.id === id ? next : item));
  const moveRecord = async (row: RecordData, to: string): Promise<boolean> => {
    try {
      const { etag } = await api.get(name, row.id);
      const { data } = await api.move(name, row.id, to, etag);
      setRow(row.id, data);
      totalsTick.set(totalsTick.peek() + 1);
      return true;
    } catch (error) {
      toast(error instanceof ApiError ? error.message : 'The move failed.', { icon: 'info' });
      return false;
    }
  };
  // With a State tab chosen, the board shows that one column, by design. Tabs by another field
  // narrow the cards and keep every column.
  const boardOnly = derive(() => state && tabSpec?.field === state.field ? tab.get() : 'all');
  const boardView = (only: string) => state ? Board<RecordData>({
    label: `${model.plural} by ${state.field}`, columns: state.states.filter(value => only === 'all' || value === only).map(value => ({ value, label: humanize(value), tint: stateTint(value) }) as BoardColumn),
    cards: rows, key: row => row.id, group: row => String(row[state.field]),
    totals: () => boardTotals.get(), amounts: Boolean(valueField),
    card: row => BoardCard({ title: () => titleOf(model, row()), tint,
      company: linkField ? () => { const id = row()[linkField.name]; return id ? ctx.links.title(linkField.target!, String(id)).get() ?? '' : ''; } : undefined,
      value: valueField ? () => formatOf(valueField, row()[valueField.name]) : undefined,
      date: dateField ? () => formatOf(dateField, row()[dateField.name]) : undefined,
      onOpen: () => open(row()) }),
    canMove: moveBlocked,
    onMove: (row, to) => {
      const before = row;
      // Shown in the new column at once; put back if the server refuses the move.
      setRow(row.id, { ...row, [state.field]: to });
      void moveRecord(before, to).then(done => { if (!done && rows.peek().some(item => item.id === before.id)) setRow(before.id, before); });
      return null;
    },
  }) : null;

  // ---------- bulk ----------

  const chosen = () => rows.peek().filter(row => selection.peek().has(row.id));
  const noun = (count: number) => count === 1 ? model.label.toLowerCase() : model.plural.toLowerCase();
  // Bulk changes stop when the page is left, and act only on records that still show what the
  // list showed: a record whose shown values changed since the list loaded is left as it is.
  const leaving = new AbortController();
  onCleanup(() => leaving.abort());
  const progress = cell('', { name: `${name}.bulk progress` });
  const shown = [...new Set([...fields.map(meta => meta.name), ...(state ? [state.field] : [])])];
  const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  /** The record's current ETag, or null when its shown values changed since the list loaded. */
  const unchangedEtag = async (row: RecordData): Promise<string | null> => {
    const { data, etag } = await api.get(name, row.id, leaving.signal);
    return shown.every(field => same(row[field], data[field])) ? etag : null;
  };
  type Outcome = { kind: 'done' } | { kind: 'changed' } | { kind: 'refused'; text: string };
  const runBulk = async (list: RecordData[], verb: string, work: (row: RecordData) => Promise<Outcome>): Promise<Outcome[] | null> => {
    const results: Outcome[] = [];
    let next = 0;
    progress.set(`${verb} 0 of ${list.length}…`);
    const worker = async () => {
      while (next < list.length && !leaving.signal.aborted) {
        const outcome = await work(list[next++]);
        if (leaving.signal.aborted) return;
        results.push(outcome);
        progress.set(`${verb} ${results.length} of ${list.length}…`);
      }
    };
    await Promise.all(Array.from({ length: Math.min(BULK_AT_ONCE, list.length) }, worker));
    if (leaving.signal.aborted) return null;
    progress.set('');
    return results;
  };
  const report = (results: Outcome[], done: string, refusedText: string) => {
    const changed = results.filter(item => item.kind === 'changed').length;
    const refused = results.flatMap(item => item.kind === 'refused' ? [item.text] : []);
    const parts = [done];
    if (changed) parts.push(`${changed} ${noun(changed)} changed since you loaded the list, so ${changed === 1 ? 'it was left as it is' : 'they were left as they are'}.`);
    if (refused.length) parts.push(`${refused.length} ${refusedText}. ${refused[0]}`);
    toast(parts.join(' '), { icon: changed || refused.length ? 'info' : 'check' });
    refresh();
  };
  const bulkDelete = () => {
    const list = chosen();
    openModal({ title: `Delete ${list.length} ${noun(list.length)}?`, icon: 'trash-2', tint: 'rose', narrow: true,
      body: () => html`<p>This can’t be undone. Records that changed since you loaded the list, or that are locked, are left as they are.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Delete', variant: 'danger', icon: 'trash-2', onClick: async () => {
        close();
        const results = await runBulk(list, 'Deleting', async row => {
          try {
            const etag = await unchangedEtag(row);
            if (etag === null) return { kind: 'changed' };
            if (leaving.signal.aborted) return { kind: 'refused', text: 'stopped' };
            await api.remove(name, row.id, etag);
            return { kind: 'done' };
          } catch (error) { return { kind: 'refused', text: `${titleOf(model, row)}: ${error instanceof ApiError ? error.message : 'not deleted'}` }; }
        });
        if (!results) return;
        const done = results.filter(item => item.kind === 'done').length;
        report(results, `Deleted ${done} ${noun(done)}.`, 'not deleted');
      } } }) });
  };
  const bulkMove = () => {
    const list = chosen();
    const targets = state!.states.filter(value => list.some(row => !moveBlocked(row, value)));
    openModal({ title: `Move ${list.length} selected`, icon: 'arrow-right', tint: 'blue', narrow: true,
      body: close => targets.length ? html`<div class="ev-choices">${targets.map(value => Button({ label: humanize(value), onClick: async () => {
        close();
        const results = await runBulk(list, 'Moving', async row => {
          const why = moveBlocked(row, value);
          if (why) return { kind: 'refused', text: why };
          try {
            const etag = await unchangedEtag(row);
            if (etag === null) return { kind: 'changed' };
            if (leaving.signal.aborted) return { kind: 'refused', text: 'stopped' };
            const { data } = await api.move(name, row.id, value, etag);
            setRow(row.id, data);
            return { kind: 'done' };
          } catch (error) { return { kind: 'refused', text: `${titleOf(model, row)}: ${error instanceof ApiError ? error.message : 'not moved'}` }; }
        });
        if (!results) return;
        const done = results.filter(item => item.kind === 'done').length;
        report(results, `Moved ${done} to ${humanize(value)}.`, 'could not move');
      } }))}</div>` : html`<p>None of the selected records has a move it can make now.</p>` });
  };
  const bulk = selectable ? BulkBar({ count: () => selection.get().size, noun: model.plural.toLowerCase(), onClear: () => selection.set(new Set()), actions: [
    ...(state?.moves.length ? [{ label: 'Move', icon: 'arrow-right', onClick: bulkMove }] : []),
    ...(canDelete ? [{ label: 'Delete', icon: 'trash-2', onClick: bulkDelete }] : []),
  ] }) : null;
  onCleanup(() => closeDialogs());

  const body = () => {
    if (failure.peek()) return EmptyState({ icon: 'info', tint: 'rose', title: `${model.plural} could not be loaded`, text: () => failure.get() ?? '', action: Button({ label: 'Try again', onClick: refresh }) });
    return EmptyState({ icon, tint, title: `No ${model.plural.toLowerCase()} yet`, text: canCreate ? `Add the first ${model.label.toLowerCase()} to get started.` : `${model.plural} you can see show up here.`,
        action: canCreate ? Button({ label: `New ${model.label.toLowerCase()}`, icon: 'plus', variant: 'primary', onClick: create }) : undefined });
  };
  // ---------- side panel ----------

  const listWidth = cell(0, { name: `${name}.list width` });
  const compactRows = () => {
    const stateMeta = state ? fieldOf(model, state.field) : undefined;
    const isCurrent = (row: RecordData) => String(row.id) === opened.get();
    const element = html`<div class="ev-compact" role="listbox" aria-label=${model.plural}>${each(rows, row => row.id, row => html`<div role="option" class="ev-crow"
      data-key=${String(row().id)} aria-selected=${() => String(isCurrent(row()))} tabindex=${() => isCurrent(row()) || (!current.get() && rows.get()[0] === row()) ? '0' : '-1'}
      on:click=${() => open(row())} on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') { event.preventDefault(); open(row()); } }}>
      <span class="l1"><b>${() => titleOf(model, row())}</b>${amountField ? html`<span class="num">${cellView(ctx, model, amountField, row)}</span>` : null}</span>
      <span class="l2">${showNumber ? html`<span class="mono">${() => String(row()[showNumber.name] ?? '')}</span>` : html`<span></span>`}${stateMeta ? cellView(ctx, model, stateMeta, row) : null}</span></div>`,
      { fallback: () => html`<p class="muted ev-note">No ${model.plural.toLowerCase()} match.</p>` })}</div>` as HTMLElement;
    element.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const items = [...element.querySelectorAll<HTMLElement>('[role="option"]')], at = items.indexOf(event.target as HTMLElement);
      const next = items[at + (event.key === 'ArrowDown' ? 1 : -1)];
      if (at < 0 || !next) return;
      event.preventDefault();
      next.focus();
      const row = rows.peek().find(item => String(item.id) === next.dataset.key);
      if (row) open(row, false);
    });
    effect(() => { const id = opened.get(); untrack(() => queueMicrotask(() => element.querySelector<HTMLElement>(`[data-key="${CSS.escape(String(id))}"]`)?.scrollIntoView?.({ block: 'nearest' }))); });
    return element;
  };

  // The table and board are built once per view; loads only change their rows.
  const mode = derive(() => failure.get() ? 'error'
    : !loading.get() && total.get() === 0 && !query.get() && !filters.active.get().length && tab.get() === 'all' ? 'empty'
    : view.get() === 'list' && panelOpen.get() ? (listWidth.get() && listWidth.get() < COMPACT_WIDTH ? 'compact' : 'narrow') : view.get());
  const content = html`<div role=${tabs ? 'tabpanel' : null} id=${() => tabs ? `ev-${name}-views-${tab.get()}-panel` : null} aria-labelledby=${() => tabs ? `ev-${name}-views-${tab.get()}` : null}>
    ${match(mode, { error: () => body(), empty: () => body(), list: () => [table(), pager], narrow: () => [table(true), pager], compact: () => [compactRows(), pager],
      board: () => [state ? html`<p class="muted ev-note ev-board-note" hidden=${() => boardOnly.get() === 'all'}>${() => `Showing ${humanize(boardOnly.get()).toLowerCase()} ${model.plural.toLowerCase()} only. Choose All ${model.plural.toLowerCase()} to see every column.`}</p>` : null,
        match(boardOnly, Object.fromEntries(['all', ...state!.states].map(value => [value, () => boardView(value)]))), when(() => (total.get() ?? 0) > BOARD_SIZE, () => html`<p class="muted ev-note">${() => `Showing the first ${BOARD_SIZE} of ${total.get()}. Use the list or a filter to see the rest.`}</p>`)] })}</div>`;

  const list = html`<div class="page ev-list" class:ev-list-narrow=${panelOpen} data-model=${name} aria-busy=${() => loading.get() ? 'true' : null}>${header}${tabs}${toolbar}<div id=${`ev-${name}-filters`}>${when(filtering, () => FilterPanel(filters, ctx.links, () => filtering.set(false)))}</div>${when(() => !panelOpen.get(), () => strip)}<p class="muted ev-progress" role="status" hidden=${() => !progress.get()}>${progress}</p>${content}${when(() => !panelOpen.get(), () => bulk)}</div>` as HTMLElement;
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(entries => listWidth.set(Math.round(entries[0]?.contentRect.width ?? 0)));
    observer.observe(list);
    onCleanup(() => observer.disconnect());
  }

  const overlay = cell(false, { name: `${name}.panel covers the list` });
  const announce = cell('', { name: `${name}.panel announcement` });
  /** Focus the row a closed panel showed, so the keyboard carries on from there. */
  const focusRow = (id: string) => queueMicrotask(() => list.querySelector<HTMLElement>(`[data-key="${CSS.escape(id)}"]`)?.focus());
  const closePanel = () => {
    const was = opened.peek();
    void router.navigate(listHref(null)).then(done => { if (done && was) focusRow(was); });
  };
  const host = (id: string): PanelHost => ({
    overlay, tab: router.route.peek()?.query.rtab,
    setTab: next => void router.navigate(listHref(id, next), { replace: true }),
    stays: to => Boolean(to && to.name === 'list' && to.params.model === name && (to.query.open ?? null) === id),
    close: closePanel,
    show: next => { refresh(); void router.navigate(listHref(String(next)), { replace: true }); },
    removed: () => { refresh(); void router.navigate(listHref(null), { replace: true }); },
  });
  // A saved, moved or changed record shows its new values in its row.
  effect(() => {
    const id = recordId(opened.get());
    if (!id) return;
    const data = api.detail(name, id).get();
    if (!data) return;
    untrack(() => {
      const list = rows.peek(), at = list.findIndex(row => row.id === id);
      if (at < 0) return;
      const changed = Object.keys(list[at]).filter(key => key in data && JSON.stringify(list[at][key]) !== JSON.stringify(data[key]));
      if (changed.length) rows.set(list.map((row, index) => index === at ? { ...row, ...Object.fromEntries(changed.map(key => [key, data[key]])) } : row));
    });
  });
  const panelLabel = () => {
    const id = opened.get(), row = rows.get().find(item => String(item.id) === id);
    return id === 'new' ? `New ${model.label.toLowerCase()}` : row ? `${model.label} ${titleOf(model, row)}` : model.label;
  };
  const panel = () => {
    const element = html`<section class="ev-panel" role="region" tabindex="-1" aria-label=${panelLabel}>
      ${each(() => opened.get() === null ? [] : [opened.get()!], id => id, id => untrack(() => RecordPage(ctx, name, id(), host(id()))))}</section>` as HTMLElement;
    // Opening moves focus into the panel, except while the arrow keys move along the list.
    effect(() => {
      const id = opened.get();
      if (id === null) return;
      untrack(() => {
        announce.set(`Opened ${panelLabel()}`);
        if (focusPanel) queueMicrotask(() => element.focus());
        focusPanel = false;
      });
    });
    return element;
  };
  const split = SplitView({ main: list, side: panel, open: panelOpen, overlay, width: ctx.prefs.cell(`panelWidth.${name}`, 480),
    label: `Resize the ${model.label.toLowerCase()} panel` });
  split.classList.add('ev-list-split');
  split.append(html`<p class="ev-sr" aria-live="polite">${announce}</p>`);
  // Escape closes the panel (dialogs and menus close first: they are outside it).
  split.addEventListener('keydown', event => {
    const target = event.target as HTMLElement;
    if (event.key !== 'Escape' || event.defaultPrevented || !panelOpen.peek() || target.closest('input, textarea, select, [contenteditable="true"]')) return;
    event.preventDefault();
    closePanel();
  });
  return split;
}

/** The id of the record of `target` whose title (or name) is `text`, or null when there is none this person can read. */
async function findRecord(ctx: AppContext, target: string, text: string): Promise<string | null> {
  let meta: ScreenModel;
  try { meta = screenModel(ctx.schema, target); } catch { return null; }
  const names = [...new Set([meta.title_field, 'name'])].filter(field => field !== 'id' && meta.fields.some(item => item.name === field));
  for (const field of names) {
    try {
      const page = await ctx.api.list(target, { filters: { [field]: text }, limit: 1 });
      const found = page.items[0];
      if (found) { ctx.links.remember?.(target, String(found.id), titleOf(meta, found)); return String(found.id); }
    } catch { /* not readable: try the next field */ }
  }
  return null;
}

/** Run `then` when `value` changes, not on the first read. */
export function changes<T>(value: () => T, then: () => void) {
  let first = true;
  effect(() => { value(); if (first) { first = false; return; } untrack(then); });
}
