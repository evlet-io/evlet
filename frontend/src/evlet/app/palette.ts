// The command palette (Cmd or Ctrl K): recent searches and a quick access grid when empty;
// records (through each model's list search), pages and actions, and preferences as you type.
// Arrow keys move through rows and the grid, Enter opens. Quick access is chosen per user.
// The Explorer and each of its views are pages here, and typing offers "Search all reports for ..." and
// "Search all data for ...", which open the Explorer with its search box filled.

import { html } from '../dom';
import { cell, derive, effect, untrack } from '../reactive';
import { Icon, SegmentedControl, Button } from '../ui/base';
import { tint as safeTint, type Tint } from '../ui/core';
import { openModal } from '../ui/layers';
import { resolveTheme } from '../ui/theme';
import { docsOn, goSettings, type AppContext } from './context';
import { identity, rootModels, titleOf, type ScreenModel } from './meta';
import { REPORT_ICON, reportsOf } from './report-page';
import { EXPLORER_VIEWS, hasLedgerReports, openExplorer } from './explorer';
import { EXPLORER_ICONS, KIND_TINTS, SHELL_ICONS } from './nav-icons';

interface Tile { id: string; group: 'Lists' | 'Documents' | 'Reports' | 'Pages'; icon: string; tint: Tint; label: string; badge?: 'plus' | 'list'; run: () => void }
interface Item { icon: string; tint: Tint | 'ghost'; label: string; sub?: string; hint?: string; run: () => void; tile?: boolean; record?: boolean; recent?: string }

const columns = () => { try { return window.matchMedia?.('(max-width: 760px)')?.matches ? 4 : 6; } catch { return 6; } };

/** Every quick access tile this user may use: lists, new records and pages. */
export function quickTiles(ctx: AppContext): Tile[] {
  const tiles: Tile[] = docsOn(ctx.session) ? [{ id: 'p-docs', group: 'Pages', icon: SHELL_ICONS.docs, tint: 'slate', label: 'Docs', run: () => void ctx.router.go('docs', {}) }] : [];
  // Controls opens in a new tab, as from the account menu, for people allowed to open it.
  if (ctx.me.peek()?.controls) tiles.push({ id: 'p-controls', group: 'Pages', icon: SHELL_ICONS.controls, tint: 'slate', label: 'Go to Controls',
    run: () => { window.open('/controls', '_blank', 'noopener'); } });
  tiles.push({ id: 'p-explorer', group: 'Pages', icon: SHELL_ICONS.explorer, tint: 'slate', label: 'Explorer', run: () => void openExplorer(ctx) });
  for (const view of EXPLORER_VIEWS) if (view.value !== 'ledger' || hasLedgerReports(ctx))
    tiles.push({ id: `p-explorer-${view.value}`, group: 'Pages', icon: view.icon, tint: 'slate', label: `Explorer: ${view.label}`, run: () => void openExplorer(ctx, view.value) });
  for (const model of rootModels(ctx.schema)) {
    const { icon, tint } = identity(model);
    tiles.push({ id: `l-${model.name}`, group: 'Lists', icon, tint, label: model.plural, badge: 'list', run: () => void ctx.router.go('list', { model: model.name }) });
    if (model.api !== 'read' && model.permissions?.create !== false) tiles.push({ id: `n-${model.name}`, group: 'Documents', icon, tint, label: `New ${model.label.toLowerCase()}`, badge: 'plus',
      run: () => void ctx.router.go('create', { model: model.name }) });
  }
  for (const report of reportsOf(ctx)) tiles.push({ id: `r-${report.name}`, group: 'Reports', icon: REPORT_ICON, tint: 'rose', label: report.label,
    run: () => void ctx.router.go('report', { report: report.name }) });
  return tiles;
}

export function openPalette(ctx: AppContext): () => void {
  if (document.querySelector('.modal.palette')) return () => {};
  // Everything is built inside the dialog's scope, so it is disposed when the palette closes.
  return openModal({ title: 'Search', className: 'palette', body: close => paletteBody(ctx, close) });
}

function paletteBody(ctx: AppContext, close: () => void) {
  const prefs = ctx.prefs;
  const all = quickTiles(ctx);
  const query = cell('', { name: 'palette.query' });
  const selected = cell(0, { name: 'palette.selected' });
  const editing = cell(false);
  const records = cell<Item[]>([]);
  const searching = cell(false);
  const capacity = () => prefs.quickRows.get() * columns();
  const chosen = derive(() => {
    const ids = prefs.quickIds.get() ?? all.filter(tile => tile.group !== 'Pages').map(tile => tile.id);
    return ids.map(id => all.find(tile => tile.id === id)).filter((tile): tile is Tile => Boolean(tile)).slice(0, capacity());
  });
  const setIds = (ids: string[]) => prefs.quickIds.set(ids.slice(0, capacity()));

  const remember = (text: string) => {
    const value = text.trim();
    if (!value) return;
    prefs.recentSearches.set([value, ...prefs.recentSearches.peek().filter(item => item.toLowerCase() !== value.toLowerCase())].slice(0, 4));
  };
  const runItem = (item: Item) => {
    if (item.recent !== undefined) { query.set(item.recent); selected.set(0); input.focus(); return; }
    if (item.record) remember(query.peek());
    close();
    item.run();
  };

  // Records: each searchable model's list endpoint, five each, the newest query wins.
  const searchable = rootModels(ctx.schema).filter(model => (model.search_fields ?? []).length);
  let ticket = 0, timer: ReturnType<typeof setTimeout> | undefined;
  effect(() => {
    const text = query.get().trim();
    clearTimeout(timer);
    const mine = ++ticket;
    if (!text) { records.set([]); searching.set(false); return; }
    searching.set(true);
    timer = setTimeout(async () => {
      const found = await Promise.all(searchable.map(async (model: ScreenModel) => {
        try {
          const page = await ctx.api.list(model.name, { q: text, limit: 5 });
          const { icon, tint } = identity(model);
          return page.items.map(row => ({ icon, tint, label: titleOf(model, row), hint: model.label, record: true,
            run: () => void ctx.router.go('record', { model: model.name, id: String(row.id) }) }) as Item);
        } catch { return [] as Item[]; }
      }));
      if (mine !== ticket) return;
      records.set(found.flat().slice(0, 8));
      searching.set(false);
    }, 180);
    return () => clearTimeout(timer);
  });

  const preferences = (): Item[] => [
    ...(ctx.me.peek() ? [{ icon: SHELL_ICONS.settings, tint: 'slate' as const, label: 'Open settings', run: () => void goSettings(ctx.router, ctx.me.peek()!.last_page || 'profile') }] : []),
    { icon: SHELL_ICONS.menu, tint: 'slate', label: 'Toggle sidebar', hint: '[', run: () => ctx.toggleSidebar() },
    ...(ctx.me.peek() && !ctx.me.peek()!.portal ? [{ icon: resolveTheme(ctx.me.peek()!.prefs.theme) === 'dark' ? 'sun' : 'moon', tint: 'slate' as const,
      label: resolveTheme(ctx.me.peek()!.prefs.theme) === 'dark' ? 'Switch to light theme' : 'Switch to dark theme', run: () => ctx.toggleTheme() }] : []),
  ];
  // Rows in the order arrow keys visit them.
  const groups = derive(() => {
    const text = query.get().trim().toLowerCase();
    if (!text) {
      const recent = editing.get() ? [] : prefs.recentSearches.get().map(entry => ({ icon: 'clock', tint: 'ghost' as const, label: entry, run: () => {}, recent: entry }));
      const tiles = editing.get() ? [] : chosen.get().map(tile => ({ icon: tile.icon, tint: tile.tint, label: tile.label, run: tile.run, tile: true, hint: tile.badge }));
      return [['Recent', recent], ['Go to', tiles]] as Array<[string, Item[]]>;
    }
    const matches = (value: string) => value.toLowerCase().includes(text);
    const pages = all.filter(tile => matches(tile.label) || matches(tile.group)).slice(0, 8)
      .map(tile => ({ icon: tile.icon, tint: tile.tint, label: tile.label, hint: tile.group === 'Documents' ? 'Create' : tile.group === 'Lists' ? 'List' : tile.group === 'Reports' ? 'Report' : 'Page', run: tile.run }));
    // Last, the Explorer's search, filled with what was typed.
    const typed = query.get().trim();
    const explorer: Item[] = [
      { icon: EXPLORER_ICONS.reports, tint: KIND_TINTS.report, label: `Search all reports for “${typed}”`, hint: 'Explorer', run: () => void openExplorer(ctx, 'reports', typed) },
      { icon: EXPLORER_ICONS.data, tint: KIND_TINTS.list, label: `Search all data for “${typed}”`, hint: 'Explorer', run: () => void openExplorer(ctx, 'data', typed) },
    ];
    return [['Records', records.get()], ['Pages and actions', pages], ['Preferences', preferences().filter(item => matches(item.label))], ['Explorer', explorer]] as Array<[string, Item[]]>;
  });
  const items = derive(() => groups.get().flatMap(([, list]) => list));
  effect(() => { items.get(); untrack(() => { if (selected.peek() >= items.peek().length) selected.set(0); }); });

  const row = (item: Item, at: number) => html`<div class="pal-it" class:on=${() => selected.get() === at} role="option" id=${`pal-${at}`}
    aria-selected=${() => String(selected.get() === at)} on:click=${() => runItem(item)} on:mousemove=${() => { if (selected.peek() !== at) selected.set(at); }}>
    <span class=${item.tint === 'ghost' ? 'ic ghost' : `ic b-${safeTint(item.tint)}`}>${Icon(item.icon, { size: 'sm' })}</span>
    <span class="lbl">${item.label}${item.sub ? html`<span class="sub">${item.sub}</span>` : null}</span>${item.hint && !item.tile ? html`<span class="hint">${item.hint}</span>` : null}
    ${item.recent !== undefined ? html`<button type="button" class="pal-rm" aria-label=${`Remove ${item.label}`} on:click=${(event: MouseEvent) => {
      event.stopPropagation(); prefs.recentSearches.set(prefs.recentSearches.peek().filter(entry => entry !== item.recent));
    }}>${Icon('x', { size: 'xs' })}</button>` : null}</div>`;
  const tile = (item: Item, at: number) => html`<div class="qa-tile" class:on=${() => selected.get() === at} role="option" id=${`pal-${at}`} aria-selected=${() => String(selected.get() === at)}
    title=${item.label} on:click=${() => runItem(item)} on:mousemove=${() => { if (selected.peek() !== at) selected.set(at); }}>
    <span class=${`qa-ic b-${safeTint(item.tint)}`}>${Icon(item.icon)}${item.hint ? html`<i class="qa-badge">${Icon(item.hint === 'plus' ? 'plus' : 'list', { size: 'xs' })}</i>` : null}</span>
    <span class="qa-lbl">${item.label}</span></div>`;

  const home = () => {
    let at = 0;
    const [[, recent], [, tiles]] = groups.get();
    const recentPart = recent.length ? html`<section class="pal-sec"><div class="pal-sh"><span>Recent</span>
      <button type="button" class="pal-link" on:click=${() => prefs.recentSearches.set([])}>Clear</button></div>${recent.map(item => row(item, at++))}</section>` : null;
    const editingNow = editing.get();
    const shown = chosen.get(), cap = capacity();
    const grid = html`<section class="pal-sec"><div class="pal-sh"><span>Go to</span>
      ${editingNow ? [SegmentedControl({ options: [{ value: '1', label: '1 row' }, { value: '2', label: '2 rows' }], value: cell(String(prefs.quickRows.peek())), size: 'small', label: 'Quick access rows',
          onChange: value => { prefs.quickRows.set(Number(value) as 1 | 2); setIds(shown.map(item => item.id)); } }),
        Button({ label: 'Done', variant: 'primary', size: 'small', onClick: () => { editing.set(false); input.focus(); } })]
      : html`<button type="button" class="pal-link" on:click=${() => editing.set(true)}>${Icon('pencil', { size: 'xs' })}Customize</button>`}</div>
      <div class="qa-grid" class:editing=${editingNow} style:--cols=${String(columns())} role=${editingNow ? null : 'listbox'} aria-label="Quick access">
        ${editingNow ? shown.map(item => html`<div class="qa-tile" title=${item.label}><span class=${`qa-ic b-${item.tint}`}>${Icon(item.icon)}${item.badge ? html`<i class="qa-badge">${Icon(item.badge === 'plus' ? 'plus' : 'list', { size: 'xs' })}</i>` : null}</span>
          <span class="qa-lbl">${item.label}</span><button type="button" class="qa-rm" aria-label=${`Remove ${item.label}`} on:click=${() => setIds(shown.filter(other => other !== item).map(other => other.id))}>${Icon('x', { size: 'xs' })}</button></div>`)
          : tiles.map(item => tile(item, at++))}
        ${editingNow ? Array.from({ length: Math.max(0, cap - shown.length) }, () => html`<div class="qa-tile empty"><span class="qa-ic">${Icon('plus')}</span><span class="qa-lbl">Empty</span></div>`) : null}
      </div>
      ${editingNow ? html`<div class="qa-picker"><div class="qa-note">${shown.length >= cap ? 'Quick access is full. Remove a tile or switch to 2 rows to add more.' : 'Pick what shows in quick access. Documents open as a list or as a new form.'}</div>
        ${(['Lists', 'Documents', 'Reports', 'Pages'] as const).map(group => html`<div class="qa-pg"><div class="qa-pgh">${group}</div><div class="qa-chips">${all.filter(item => item.group === group).map(item => {
          const on = shown.includes(item);
          return html`<button type="button" class="qa-chip" class:on=${on} aria-pressed=${String(on)} disabled=${!on && shown.length >= cap}
            on:click=${() => setIds(on ? shown.filter(other => other !== item).map(other => other.id) : [...shown.map(other => other.id), item.id])}>
            <span class=${`c-${item.tint}`} style="display:inline-flex">${Icon(item.icon, { size: 'xs' })}</span>${item.label}${on ? Icon('check', { size: 'xs' }) : null}</button>`;
        })}</div></div>`)}</div>` : null}</section>`;
    return [recentPart, grid];
  };
  const results = () => {
    let at = 0;
    const list = groups.get().filter(([, entries]) => entries.length);
    const section = ([name, entries]: [string, Item[]]) => html`<section class="pal-sec" role="group" aria-label=${name}><div class="pal-sh"><span>${name}</span></div>${entries.map(item => row(item, at++))}</section>`;
    // With nothing found, the Explorer's search is still offered under the message.
    if (list.every(([name]) => name === 'Explorer')) return [searching.get() ? html`<div class="pal-empty">${Icon('search', { size: 'lg' })}<div>Searching…</div></div>`
      : html`<div class="pal-empty">${Icon('search', { size: 'lg' })}<div>${`No results for “${query.peek().trim()}”`}</div><span>Try a record title or a page name.</span></div>`, list.map(section)];
    return list.map(section);
  };

  const keydown = (event: KeyboardEvent) => {
    const list = items.peek(), at = selected.peek(), current = list[at], cols = columns();
    const move = (to: number) => { event.preventDefault(); selected.set(Math.max(0, Math.min(list.length - 1, to))); document.getElementById(`pal-${selected.peek()}`)?.scrollIntoView?.({ block: 'nearest' }); };
    if (event.key === 'ArrowDown') move(current?.tile && list[at + cols]?.tile ? at + cols : current?.tile ? at : at + 1);
    else if (event.key === 'ArrowUp') move(current?.tile && list[at - cols]?.tile ? at - cols : current?.tile ? list.findIndex(item => item.tile) - 1 : at - 1);
    else if (event.key === 'ArrowRight' && current?.tile && !input.value) move(at + 1);
    else if (event.key === 'ArrowLeft' && current?.tile && !input.value) move(at - 1);
    else if (event.key === 'Enter') { event.preventDefault(); if (current) runItem(current); }
  };
  // Cmd or Ctrl K again closes it.
  const toggle = (event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); event.stopPropagation(); close(); return; }
    keydown(event);
  };
  const input = html`<input placeholder="Search records, pages and actions" autocomplete="off" spellcheck="false" aria-label="Search"
    role="combobox" aria-expanded="true" aria-controls="ev-pal-list" aria-activedescendant=${() => items.get().length ? `pal-${selected.get()}` : null}
    prop:value=${query} on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); selected.set(0); editing.set(false); }} on:keydown=${toggle}>` as HTMLInputElement;

  return html`<div class="pal-in">${Icon('search', { size: 'lg' })}${input}<kbd>Esc</kbd></div>
    <div class="pal-list" id="ev-pal-list" role="listbox" aria-label="Results">${() => query.get().trim() ? results() : home()}</div>
    <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> Move</span><span><kbd>↵</kbd> Open</span><span class="hide-sm"><kbd>⌘</kbd><kbd>K</kbd> Toggle</span></div>`;
}
