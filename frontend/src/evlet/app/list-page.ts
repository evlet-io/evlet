// The generated list page of a model (docs/ui-design/README.md section 6, List pages): page header,
// saved views by State, toolbar with search and filters, the model's measures, a DataTable with
// the list fields (title pinned), sorting, paging and a bulk bar, or a board by State.
//
// Search, sort, page, view, saved view and filters live in the address, so a link opens the
// same list. Everything runs on the server: the browser never filters or sorts a partial list.

import { html, match, when, type Child } from '../dom';
import { ApiError, type RecordData } from '../api';
import { batch, cell, derive, effect, onCleanup, untrack, type Cell } from '../reactive';
import { Button, EmptyState, Icon, IconButton, SearchInput, SegmentedControl, StatusPill, SummaryStrip, type StatProps } from '../ui/base';
import { displayValue, localField, stateTint } from '../ui/fields';
import { humanize } from '../ui/kinds';
import { closeDialogs, DialogFooter, Menu, openModal, openPopover, toast } from '../ui/layers';
import { Board, BoardCard, BulkBar, Filters, PageHeader, Toolbar, type ActiveFilter, type BoardColumn } from '../ui/list';
import { Tabs } from '../ui/doc';
import { DataTable, emptyPrefs, type Column, type Sort, type TablePrefs } from '../ui/table';
import type { FieldSchema } from '../model/schema';
import type { AppContext } from './context';
import { fieldOf, formatOf, identity, isStateField, keyFields, listFields, measureLabel, measureText, screenModel, sources, titleOf, type ScreenModel } from './meta';

export const PAGE_SIZE = 50;
const BOARD_SIZE = 100;

interface Filter extends ActiveFilter { field: string; value: unknown }

/** A list cell: state pill, record title for links, formatted value. */
export function cellView(ctx: AppContext, model: ScreenModel, meta: FieldSchema, row: () => RecordData): Child {
  if (isStateField(model, meta)) return () => {
    const value = row()[meta.name];
    return value === null || value === undefined ? '' : StatusPill({ label: humanize(String(value)), tint: stateTint(String(value)) });
  };
  if (meta.type === 'link' || meta.type === 'tags' || meta.type === 'boolean' || meta.type === 'state') {
    const field = localField(meta as any, untrack(() => row()[meta.name]), {});
    effect(() => field.value.set(row()[meta.name]));
    return displayValue(field, { links: ctx.links, empty: '' });
  }
  return () => formatOf(meta, row()[meta.name]);
}

export function ListPage(ctx: AppContext, name: string): HTMLElement {
  const { api, router } = ctx;
  const model = screenModel(ctx.schema, name);
  const { icon, tint } = identity(model);
  const state = model.state && fieldOf(model, model.state.field) ? model.state : null;
  const canCreate = model.permissions?.create !== false;
  const canDelete = model.permissions?.delete === true;
  const fields = listFields(model);
  const start = router.route.peek()?.query ?? {};
  ctx.crumbs.set([{ label: model.plural }]);

  // ---------- the list's state, from and to the address ----------

  const q = cell(start.q ?? '', { name: `${name}.q` });
  const sortFromUrl = (value: string | undefined): Sort => !value ? null : value.startsWith('-') ? { key: value.slice(1), dir: 'desc' } : { key: value, dir: 'asc' };
  const sort = cell<Sort>(sortFromUrl(start.sort), { name: `${name}.sort` });
  const page = cell(Math.max(1, Number(start.page) || 1), { name: `${name}.page` });
  const view = cell<'list' | 'board'>(state && start.view === 'board' ? 'board' : 'list', { name: `${name}.view` });
  const tab = cell(state && start.tab && state.states.includes(start.tab) ? start.tab : 'all', { name: `${name}.tab` });
  const filters = cell<Filter[]>(parseFilters(model, start.f), { name: `${name}.filters` });
  const selection = cell(new Set<string | number>(), { name: `${name}.selection` });
  const query = cell(q.peek(), { name: `${name}.q (debounced)` });
  let typing: ReturnType<typeof setTimeout> | undefined;
  changes(() => q.get(), () => { clearTimeout(typing); typing = setTimeout(() => batch(() => { query.set(q.peek()); page.set(1); }), 250); });
  onCleanup(() => clearTimeout(typing));

  const serverFilters = derive(() => {
    const result: Record<string, unknown> = {};
    for (const filter of filters.get()) result[filter.field] = filter.value;
    if (state && tab.get() !== 'all') result[state.field] = tab.get();
    return result;
  });
  effect(() => {
    const params: Record<string, string> = {};
    if (query.get()) params.q = query.get();
    const order = sort.get();
    if (order) params.sort = `${order.dir === 'desc' ? '-' : ''}${order.key}`;
    if (page.get() > 1) params.page = String(page.get());
    if (view.get() === 'board') params.view = 'board';
    if (tab.get() !== 'all') params.tab = tab.get();
    if (filters.get().length) params.f = JSON.stringify(Object.fromEntries(filters.get().map(filter => [filter.field, filter.value])));
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
    const board = view.get() === 'board';
    const request = { q: query.get() || undefined, filters: serverFilters.get(), limit: board ? BOARD_SIZE : PAGE_SIZE,
      offset: board ? 0 : (page.get() - 1) * PAGE_SIZE, orderBy: board ? undefined : sort.get() ? `${sort.get()!.dir === 'desc' ? '-' : ''}${sort.get()!.key}` : undefined };
    reload.get();
    const mine = ++ticket;
    loading.set(true);
    void api.list(name, request).then(result => {
      if (mine !== ticket) return;
      batch(() => { rows.set(result.items); total.set(result.total); failure.set(null); loading.set(false); selection.set(new Set()); });
      // A page past the end (after deletes or a narrower filter) goes back to the last page.
      if (!board && !result.items.length && result.total > 0 && page.peek() > 1) page.set(Math.ceil(result.total / PAGE_SIZE));
    }, (error: unknown) => {
      if (mine !== ticket || (error instanceof ApiError && error.kind === 'aborted')) return;
      batch(() => { failure.set(error instanceof ApiError ? error.message : 'The list could not be loaded.'); loading.set(false); });
    });
  });
  const refresh = () => reload.set(reload.peek() + 1);

  // Model-level measures (Phase 9) follow the list's filters.
  const declared = (model.measures ?? []).filter(measure => !measure.per_record);
  const measures = cell<Record<string, unknown> | null>(null);
  if (declared.length) effect(() => {
    const current = serverFilters.get();
    reload.get();
    void api.measures(name, current).then(values => measures.set(values), () => measures.set(null));
  });
  // Saved view counts by state, when the aggregate route is there (Phase 9).
  const counts = cell<Record<string, number> | null>(null);
  if (state && model.measures) effect(() => {
    const others = { ...serverFilters.get() };
    delete others[state.field];
    reload.get();
    void api.aggregate(name, ['n:count'], [state.field], others).then(result => {
      counts.set(Object.fromEntries(result.items.map(item => [String(item[state.field]), Number(item.n)])));
    }, () => counts.set(null));
  });

  // ---------- header, views and toolbar ----------

  const create = () => void router.go('create', { model: name });
  if (canCreate) ctx.createHere.set(create);
  onCleanup(() => { if (ctx.createHere.peek() === create) ctx.createHere.set(null); });

  const filterable = model.fields.filter(field => !field.hidden && !isStateField(model, field) && ((field.type === 'select' && field.choices?.length) || field.type === 'boolean'));
  const addFilter = (meta: FieldSchema, value: unknown) => {
    const label = `${meta.label}: ${meta.type === 'boolean' ? (value ? 'Yes' : 'No') : humanize(String(value))}`;
    batch(() => { filters.set([...filters.peek().filter(item => item.field !== meta.name), { key: meta.name, field: meta.name, value, label }]); page.set(1); });
  };
  const filterMenu = (anchor: HTMLElement) => openPopover(anchor, close => Menu(filterable.flatMap(meta => [
    { heading: meta.label },
    ...(meta.type === 'boolean' ? [true, false] : meta.choices!).map(value => ({ label: meta.type === 'boolean' ? (value ? 'Yes' : 'No') : humanize(String(value)), onSelect: () => addFilter(meta, value) })),
  ]), close), { label: 'Filter' });

  const header = PageHeader({ icon, tint, title: model.plural, count: () => total.get(), actions: [
    state ? SegmentedControl({ options: [{ value: 'list', label: 'List', icon: 'list' }, { value: 'board', label: 'Board', icon: 'kanban' }], value: view, label: 'View',
      onChange: next => batch(() => { view.set(next); selection.set(new Set()); }) }) : null,
    filterable.length ? Button({ label: 'Filter', icon: 'sliders-horizontal', hideLabelOnPhone: true, onClick: event => filterMenu(event.currentTarget as HTMLElement) }) : null,
    canCreate ? Button({ label: `New ${model.label.toLowerCase()}`, icon: 'plus', variant: 'primary', hideLabelOnPhone: true, onClick: create }) : null,
  ].filter(Boolean) as Child[] });

  const tabs = state ? Tabs({ label: 'Saved views', active: tab, idPrefix: `ev-${name}-views`, tabs: [
    { id: 'all', label: `All ${model.plural.toLowerCase()}`, count: () => counts.get() ? Object.values(counts.get()!).reduce((sum, value) => sum + value, 0) : tab.get() === 'all' ? total.get() : null },
    ...state.states.map(value => ({ id: value, label: humanize(value), count: () => counts.get() ? counts.get()![value] ?? 0 : tab.get() === value ? total.get() : null })),
  ] }) : null;
  changes(() => tab.get(), () => batch(() => { page.set(1); selection.set(new Set()); }));

  const search = (model.search_fields ?? []).length ? SearchInput({ value: q, placeholder: `Search ${model.plural.toLowerCase()}`, label: `Search ${model.plural.toLowerCase()}` }) : null;
  if (search) {
    ctx.pageSearch.set(search.querySelector('input'));
    onCleanup(() => { if (ctx.pageSearch.peek()?.isConnected === false || ctx.pageSearch.peek() === search.querySelector('input')) ctx.pageSearch.set(null); });
  }
  const clearAll = () => batch(() => { filters.set([]); q.set(''); tab.set('all'); page.set(1); });
  const toolbar = Toolbar({ label: `${model.plural} tools`, left: [search, Filters({ filters: filters as unknown as Cell<ActiveFilter[]>, onChange: () => page.set(1) })],
    right: [html`<span class="summary" aria-live="polite">${() => loading.get() ? 'Loading…' : total.get() === null ? '' : `${total.get()} ${total.get() === 1 ? model.label.toLowerCase() : model.plural.toLowerCase()}`}</span>`] });

  const strip = declared.length ? when(() => measures.get() !== null, () => SummaryStrip(declared.slice(0, 4).map(measure => ({
    label: measureLabel(measure), value: () => measureText(ctx.schema, model, measure, measures.get()?.[measure.name]),
    note: measure.where ? 'Filtered by a rule' : undefined,
  }) as StatProps))) : null;

  // ---------- table ----------

  const open = (row: RecordData) => void router.go('record', { model: name, id: String(row.id) });
  const columns: Column<RecordData>[] = fields.map((meta, index) => ({
    key: meta.name, label: meta.label, align: ['currency', 'decimal', 'integer', 'percent', 'duration'].includes(meta.type) ? 'right' : 'left',
    width: index === 0 ? 240 : meta.type === 'currency' ? 140 : 160, value: (row: RecordData) => row[meta.name],
    sortable: meta.stored !== false && meta.type !== 'tags' && meta.type !== 'longtext',
    render: index === 0 && meta.name === model.title_field
      ? (row: () => RecordData) => html`<a class="ev-row-link" href=${() => router.href('record', { model: name, id: String(row().id) })}>${() => titleOf(model, row())}</a>`
      : (row: () => RecordData) => cellView(ctx, model, meta, row),
  }));
  const tablePrefs = ctx.prefs.cell<TablePrefs>(`list.${name}`, emptyPrefs());
  const selectable = canDelete || Boolean(state?.moves.length);
  const table = () => DataTable<RecordData>({ columns, rows, key: row => row.id, label: model.plural, pinned: 1, selectable, selection, sort, serverSort: true,
    onOpen: open, prefs: tablePrefs, height: 640,
    empty: () => EmptyState({ icon: 'search', tint: 'slate', title: `No ${model.plural.toLowerCase()} match`, text: 'Try a different search or clear the filters.',
      action: Button({ label: 'Clear filters', onClick: clearAll }) }) });
  changes(() => sort.get(), () => page.set(1));

  const pages = derive(() => Math.max(1, Math.ceil((total.get() ?? 0) / PAGE_SIZE)));
  const pager = html`<nav class="ev-pager" aria-label="Pages" hidden=${() => (total.get() ?? 0) <= PAGE_SIZE}>
    <span class="num">${() => { const from = (page.get() - 1) * PAGE_SIZE + 1; return `${from}–${Math.min(total.get() ?? 0, from + PAGE_SIZE - 1)} of ${total.get() ?? 0}`; }}</span>
    ${IconButton({ icon: 'chevron-left', tip: 'Previous page', disabled: () => page.get() <= 1, onClick: () => page.set(Math.max(1, page.peek() - 1)) })}
    ${IconButton({ icon: 'chevron-right', tip: 'Next page', disabled: () => page.get() >= pages.get(), onClick: () => page.set(Math.min(pages.peek(), page.peek() + 1)) })}</nav>`;

  // ---------- board ----------

  const sumMeasure = declared.find(measure => measure.function === 'sum' && measure.field && !measure.where) ?? declared.find(measure => measure.function === 'sum' && measure.field);
  const valueField = sumMeasure?.field ? fieldOf(model, sumMeasure.field) : undefined;
  const keys = keyFields(model);
  const linkField = keys.find(meta => meta.type === 'link');
  const dateField = keys.find(meta => meta.type === 'date') ?? model.fields.find(meta => meta.type === 'date');
  const role = ctx.user.role;
  const moveBlocked = (row: RecordData, to: string): string | null => {
    const from = String(row[state!.field]);
    const move = state!.moves.find(item => item.to === to && sources(item).includes(from));
    if (!move) return `${titleOf(model, row)} cannot move from ${humanize(from)} to ${humanize(to)}`;
    if (move.roles && !move.roles.includes(role)) return `Your role cannot use ${move.label}`;
    if (model.permissions?.update === false) return 'You cannot change this record';
    return null;
  };
  const setRow = (id: number, next: RecordData) => rows.set(rows.peek().map(item => item.id === id ? next : item));
  const moveRecord = async (row: RecordData, to: string): Promise<boolean> => {
    try {
      const { etag } = await api.get(name, row.id);
      const { data } = await api.move(name, row.id, to, etag);
      setRow(row.id, data);
      return true;
    } catch (error) {
      toast(error instanceof ApiError ? error.message : 'The move failed.', { icon: 'info' });
      return false;
    }
  };
  const boardView = () => state ? Board<RecordData>({
    label: `${model.plural} by ${state.field}`, columns: state.states.map(value => ({ value, label: humanize(value), tint: stateTint(value) }) as BoardColumn),
    cards: rows, key: row => row.id, group: row => String(row[state.field]),
    measure: valueField ? row => Number(row[valueField.name] ?? 0) || 0 : undefined,
    card: row => BoardCard({ title: () => titleOf(model, row()), tint,
      company: linkField ? () => { const id = row()[linkField.name]; return id ? ctx.links.title(linkField.target!, Number(id)).get() ?? '' : ''; } : undefined,
      value: valueField ? () => formatOf(valueField, row()[valueField.name]) : undefined,
      date: dateField ? () => formatOf(dateField, row()[dateField.name]) : undefined,
      onOpen: () => open(row()) }),
    onMove: (row, to) => {
      const why = moveBlocked(row, to);
      if (why) return why;
      const before = row;
      // Shown in the new column at once; put back if the server refuses the move.
      setRow(row.id, { ...row, [state.field]: to });
      void moveRecord(before, to).then(done => { if (!done && rows.peek().some(item => item.id === before.id)) setRow(before.id, before); });
      return null;
    },
  }) : null;

  // ---------- bulk ----------

  const chosen = () => rows.peek().filter(row => selection.peek().has(row.id));
  const bulkDelete = () => {
    const list = chosen();
    openModal({ title: `Delete ${list.length} ${list.length === 1 ? model.label.toLowerCase() : model.plural.toLowerCase()}?`, icon: 'trash-2', tint: 'rose', narrow: true,
      body: () => html`<p>This can’t be undone. Records that others changed or that are locked are left as they are.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Delete', variant: 'danger', icon: 'trash-2', onClick: async () => {
        close();
        let done = 0; const refused: string[] = [];
        for (const row of list) {
          try { const { etag } = await api.get(name, row.id); await api.remove(name, row.id, etag); done++; }
          catch (error) { refused.push(`${titleOf(model, row)}: ${error instanceof ApiError ? error.message : 'not deleted'}`); }
        }
        toast(refused.length ? `Deleted ${done}. ${refused.length} not deleted. ${refused[0]}` : `Deleted ${done} ${done === 1 ? model.label.toLowerCase() : model.plural.toLowerCase()}`, { icon: refused.length ? 'info' : 'trash-2' });
        refresh();
      } } }) });
  };
  const bulkMove = () => {
    const list = chosen();
    const targets = state!.states.filter(value => list.some(row => !moveBlocked(row, value)));
    openModal({ title: `Move ${list.length} selected`, icon: 'arrow-right', tint: 'blue', narrow: true,
      body: close => targets.length ? html`<div class="ev-choices">${targets.map(value => Button({ label: humanize(value), onClick: async () => {
        close();
        let done = 0; const refused: string[] = [];
        for (const row of list) {
          const why = moveBlocked(row, value);
          if (why) { refused.push(why); continue; }
          if (await moveRecord(row, value)) done++; else refused.push(titleOf(model, row));
        }
        toast(refused.length ? `Moved ${done} to ${humanize(value)}. ${refused.length} could not move.` : `Moved ${done} to ${humanize(value)}`, { icon: refused.length ? 'info' : 'check' });
        refresh();
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
  // The table and board are built once per view; loads only change their rows.
  const mode = derive(() => failure.get() ? 'error'
    : !loading.get() && total.get() === 0 && !query.get() && !filters.get().length && tab.get() === 'all' ? 'empty' : view.get());
  const content = html`<div role=${tabs ? 'tabpanel' : null} id=${() => tabs ? `ev-${name}-views-${tab.get()}-panel` : null} aria-labelledby=${() => tabs ? `ev-${name}-views-${tab.get()}` : null}>
    ${match(mode, { error: () => body(), empty: () => body(), list: () => [table(), pager],
      board: () => [boardView(), when(() => (total.get() ?? 0) > BOARD_SIZE, () => html`<p class="muted ev-note">${() => `Showing the first ${BOARD_SIZE} of ${total.get()}. Use the list or a filter to see the rest.`}</p>`)] })}</div>`;

  return html`<div class="page ev-list" data-model=${name} aria-busy=${() => loading.get() ? 'true' : null}>${header}${tabs}${toolbar}${strip}${content}${bulk}</div>` as HTMLElement;
}

function parseFilters(model: ScreenModel, text: string | undefined): Filter[] {
  if (!text) return [];
  try {
    const raw = JSON.parse(text) as Record<string, unknown>;
    return Object.entries(raw).flatMap(([name, value]) => {
      const meta = fieldOf(model, name);
      if (!meta || isStateField(model, meta)) return [];
      return [{ key: name, field: name, value, label: `${meta.label}: ${meta.type === 'boolean' ? (value ? 'Yes' : 'No') : humanize(String(value))}` }];
    });
  } catch { return []; }
}

/** Run `then` when `value` changes, not on the first read. */
export function changes<T>(value: () => T, then: () => void) {
  let first = true;
  effect(() => { value(); if (first) { first = false; return; } untrack(then); });
}
