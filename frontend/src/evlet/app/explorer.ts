// The Explorer (docs/ui.md#explorer): every report and every model this person can open, from every
// app, as searchable cards, and a Dashboards view. It reads only /api/schema, so it costs no request.
//
// It lives at /?explorer=<view>: the root path is the one desk path the server always serves, and the
// query names the view, so a link or a refresh opens the same view without a server route.

import { html } from '../dom';
import { cell, derive, effect, untrack } from '../reactive';
import { EmptyState, IconTile, SearchInput, SegmentedControl } from '../ui/base';
import { PageHeader, Toolbar } from '../ui/list';
import type { AppContext, AppRouter } from './context';
import { identity, rootModels } from './meta';
import { REPORT_ICON, reportsOf } from './report-page';
import { appOf, appsOf, modelCounts, reportApp } from './workspaces';
import type { Tint } from '../ui/core';

export type ExplorerView = 'reports' | 'data' | 'dashboards';

// The views in order. Ledger reports (reports over ledger models) join here as their own view once the
// server offers a read-only, permission-checked way to list them; nothing of it shows until then.
const VIEWS: Array<{ value: ExplorerView; label: string; icon: string }> = [
  { value: 'reports', label: 'Reports', icon: REPORT_ICON },
  { value: 'data', label: 'Data', icon: 'table-2' },
  { value: 'dashboards', label: 'Dashboards', icon: 'layout-grid' },
];
const viewOf = (value: string | undefined): ExplorerView => VIEWS.some(view => view.value === value) ? value as ExplorerView : 'reports';

/** The Explorer's address: /?explorer=reports. */
export const explorerHref = (router: AppRouter, view: ExplorerView = 'reports') => router.href('home', {}, { explorer: view });

/** Whether a route is the Explorer. */
export const isExplorer = (route: { name: string; query: Record<string, string> } | null | undefined) => route?.name === 'home' && Boolean(route.query.explorer);

interface Card { key: string; label: string; app: string; appLabel: string; description: string; href: string; icon: string; tint: Tint; count?: () => number | undefined }

export function ExplorerPage(ctx: AppContext): HTMLElement {
  const { router } = ctx;
  const application = ctx.session.application ?? {};
  const fallback = application.playground ? 'Playground' : application.name || 'Evlet';
  const apps = appsOf(ctx.schema.schema, fallback);
  const labelOf = (key: string) => apps.find(app => app.key === key)?.label ?? fallback;
  const view = cell<ExplorerView>(viewOf(router.route.peek()?.query.explorer), { name: 'explorer.view' });
  const query = cell('', { name: 'explorer.query' });
  const app = cell('', { name: 'explorer.app' });
  ctx.crumbs.set([{ label: 'Explorer' }]);
  // Back and Forward change the view in the address.
  effect(() => { const next = router.route.get(); if (isExplorer(next)) untrack(() => { if (view.peek() !== viewOf(next!.query.explorer)) view.set(viewOf(next!.query.explorer)); }); });

  const counts = modelCounts(ctx);
  const reports: Card[] = reportsOf(ctx).map(report => {
    const key = reportApp(ctx.schema.schema, report);
    return { key: report.name, label: report.label, app: key, appLabel: labelOf(key), description: report.description ?? '',
      href: router.href('report', { report: report.name }), icon: REPORT_ICON, tint: 'rose' };
  });
  // Child tables are never listed. Ledger models have no public surface, so the schema never carries them.
  const models: Card[] = rootModels(ctx.schema).map(model => {
    const { icon, tint } = identity(model);
    return { key: model.name, label: model.plural, app: appOf(model), appLabel: labelOf(appOf(model)), description: model.description ?? '',
      href: router.href('list', { model: model.name }), icon, tint,
      count: () => ctx.me.get()?.prefs.counts ? counts.get()[model.name] : undefined };
  });

  const matching = (cards: Card[]) => derive(() => {
    const text = query.get().trim().toLowerCase(), only = app.get();
    return cards.filter(card => (!only || card.app === only)
      && (!text || [card.label, card.description, card.appLabel].some(value => value.toLowerCase().includes(text))));
  });
  const shownReports = matching(reports), shownModels = matching(models);

  const card = (item: Card) => html`<a class="ex-card" href=${item.href}>
    <span class="ex-card-h">${IconTile(item.icon, { tint: item.tint, size: 28 })}<b>${item.label}</b>
      ${() => { const count = item.count?.(); return count === undefined ? null : html`<span class="count num" title="Records">${String(count)}</span>`; }}</span>
    ${item.description ? html`<span class="ex-desc">${item.description}</span>` : null}
    <span class="ex-app">${item.appLabel}</span></a>`;
  const grid = (all: Card[], shown: () => Card[], none: { title: string; text: string }) => () => {
    if (!all.length) return EmptyState({ icon: 'search', tint: 'slate', title: none.title, text: none.text });
    const list = shown();
    return list.length ? html`<div class="ex-grid">${list.map(card)}</div>`
      : EmptyState({ icon: 'search', tint: 'slate', title: 'Nothing matches', text: 'Try another word or app.' });
  };
  // Built once, so typing never rebuilds the search box; the cards below follow the search and the view.
  const search = SearchInput({ value: query, placeholder: 'Search', label: 'Search by name, description or app' });
  const tools = Toolbar({ label: 'Explorer search', left: [search,
    html`<select class="input ex-app-filter" aria-label="App" on:change=${(event: Event) => app.set((event.target as HTMLSelectElement).value)}>
      <option value="" selected=${() => app.get() === ''}>All apps</option>
      ${apps.map(entry => html`<option value=${entry.key} selected=${() => app.get() === entry.key}>${entry.label}</option>`)}</select>`,
  ] });
  const searchable = () => view.get() !== 'dashboards';
  const body = () => {
    switch (view.get()) {
      case 'reports': return grid(reports, () => shownReports.get(), { title: 'No reports', text: 'Your role cannot run any report here.' });
      case 'data': return grid(models, () => shownModels.get(), { title: 'No data', text: 'Your role cannot open any list here.' });
      case 'dashboards': return EmptyState({ icon: 'layout-grid', tint: 'slate', title: 'No dashboards yet', text: 'Dashboards will be listed here when an app adds them.' });
    }
  };
  return html`<div class="page ev-explorer">${PageHeader({ icon: 'layout-grid', tint: 'slate', title: 'Explorer' })}
    <div class="ex-views">${SegmentedControl({ options: VIEWS, value: view, label: 'Explorer view',
      onChange: next => void router.go('home', {}, { explorer: next }) })}</div>
    <div class="ex-tools" hidden=${() => !searchable()}>${tools}</div>${body}</div>` as HTMLElement;
}
