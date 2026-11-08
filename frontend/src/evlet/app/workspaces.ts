// Workspaces: what the sidebar shows, three levels deep (workspace > section > shortcut), and the
// apps the Explorer groups reports and models by.
//
// workspaceNav(schema, links) is the one place the sidebar's structure comes from. It maps the
// workspaces /api/schema declares (docs/ui.md, Workspaces): a model item opens its list (with its
// board view and filters in the list address), a report its page, a slip opens over the page, and a URL
// item its address. An older server without `workspaces` gets the fallback: one workspace per installed
// app, with Documents, Records and Reports sections. The shell only reads the returned shape.

import { cell, type Cell } from '../reactive';
import type { AppSchema, ModelSchema, ReportSchema, WorkspaceItem, WorkspaceSchema } from '../model/schema';
import { tintFor, type Tint } from '../ui/core';
import { fieldOf, groupOf, type ScreenModel } from './meta';
import { KIND_ICONS, SHELL_ICONS, kindOf, type ShortcutKind } from './nav-icons';
import type { AppContext } from './context';

export interface NavShortcut {
  key: string; label: string; href: string;
  /** What it opens; its icon is the kind's (app/nav-icons.ts), never the item's. */
  kind: ShortcutKind; icon: string;
  /** The model whose plain list this is: its record count can show beside it. */
  model?: string;
  /** A menu slip: the shortcut presents it over the page instead of opening an address. */
  slip?: string;
  /** An http(s) address: it opens in a new tab. */
  external?: boolean;
  /** What the page shows when its address leaves a part out, such as the list's home tab: the address the list
   * settles on drops ?tab= for it, and the shortcut still matches. */
  defaults?: Record<string, string>;
}
export interface NavSection { key: string; label: string; icon: string; count?: number; items: NavShortcut[] }
export interface Workspace { key: string; label: string; icon: string; tint?: Tint; sections: NavSection[];
  /** custom: added on this site in the workspace editor; absent for the fallback. */ source?: WorkspaceSchema['source'] }
export interface WorkspaceNav { workspaces: Workspace[] }

/** How a shortcut's address is made: the shell passes the router's. */
export interface NavLinks { list(model: string, query?: Record<string, string>): string; report(name: string): string }

const SECTION_ICONS: Record<string, string> = { Documents: 'file-text', Records: 'table-2', Reports: KIND_ICONS.report };

/** "sales_crm" -> "Sales crm". */
export const appLabel = (key: string) => key.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());

/** The installed app a model belongs to: the first part of its resource id, or '' for a model without one. */
export const appOf = (model: Pick<ModelSchema, 'resource'>) => typeof model.resource === 'string' && model.resource.includes('.') ? model.resource.split('.')[0] : '';

/** The app a report belongs to: the app part of an installed app's report name ("showcase.ClientCredit"),
 * else its model's app. A report with neither goes with the first app. */
export function reportApp(schema: AppSchema, report: ReportSchema): string {
  const apps = new Set(schema.models.filter(item => !item.child_only).map(appOf));
  const prefix = report.name.includes('.') ? report.name.split('.')[0] : '';
  if (prefix && apps.has(prefix)) return prefix;
  const model = report.model ? schema.models.find(item => item.name === report.model) : undefined;
  if (model) return appOf(model);
  const first = schema.models.find(item => !item.child_only);
  return first ? appOf(first) : '';
}

/** The apps in schema order, each with a label. The '' app (models without a resource id) uses fallback. */
export function appsOf(schema: AppSchema, fallback: string): Array<{ key: string; label: string }> {
  const keys = [...new Set(schema.models.filter(model => !model.child_only).map(appOf))];
  return keys.map(key => ({ key, label: key ? appLabel(key) : fallback }));
}

/** The sidebar's structure: the declared workspaces, or for a server without them the fallback.
 * fallback names the app of models without a resource id (the application's name). */
export function workspaceNav(schema: AppSchema, links: NavLinks, fallback = 'Workspace'): WorkspaceNav {
  if (!Array.isArray(schema.workspaces)) return fallbackNav(schema, links, fallback);
  return { workspaces: schema.workspaces.map((workspace): Workspace => ({
    key: workspace.key, label: workspace.label, icon: workspace.icon || SHELL_ICONS.workspace, tint: tintFor(workspace.key), source: workspace.source,
    sections: workspace.groups.map(group => ({ key: group.key, label: group.label, icon: group.icon || 'layers',
      items: group.items.map(item => shortcutOf(schema, item, links)) })).filter(section => section.items.length),
  })).filter(workspace => workspace.sections.length) };
}

/** One declared item as a sidebar shortcut, with its kind's icon. */
export function shortcutOf(schema: AppSchema, item: WorkspaceItem, links: NavLinks): NavShortcut {
  const kind = kindOf(item), base = { key: item.key, label: item.label, kind, icon: KIND_ICONS[kind] };
  switch (item.kind) {
    case 'model': {
      const model = schema.models.find(entry => entry.name === item.model) as unknown as ScreenModel | undefined;
      const query = listQuery(model, item.view, item.filters);
      const home = model?.list_tabs?.default;
      return { ...base, href: links.list(item.model, query),
        model: Object.keys(query).length ? undefined : item.model, ...(home && home !== 'all' ? { defaults: { tab: home } } : {}) };
    }
    case 'report': return { ...base, href: links.report(item.report) };
    case 'slip': return { ...base, href: '', slip: item.slip };
    default: return { ...base, href: item.url, external: /^https?:/i.test(item.url) };
  }
}

/**
 * The list address of a model shortcut: view=board, and its filters as the list keeps them. An equality on the
 * field of the list tabs (Meta.list_tabs) opens that tab; the rest go in ?f= in the form the list's filter rows
 * write back (a Boolean equality is "is yes" or "is no", a Select equality is "in"), so the address the list
 * settles on still matches the shortcut.
 */
export function listQuery(model: ScreenModel | undefined, view: 'list' | 'board', filters: unknown[] | null): Record<string, string> {
  const query: Record<string, string> = {};
  if (view === 'board') query.view = 'board';
  const rest: unknown[] = [];
  for (const entry of filters ?? []) {
    if (!Array.isArray(entry) || entry.length !== 3) { rest.push(entry); continue; }
    const [name, op, value] = entry as [string, string, unknown];
    const meta = model ? fieldOf(model, name) : undefined;
    if (op === 'eq' && model?.list_tabs?.field === name && !query.tab && (typeof value === 'string' || typeof value === 'number')) query.tab = String(value);
    else if (op === 'eq' && meta?.type === 'boolean' && typeof value === 'boolean') rest.push([name, value ? 'yes' : 'no', null]);
    else if (op === 'eq' && meta?.type === 'select' && value !== null) rest.push([name, 'in', [value]]);
    else rest.push(entry);
  }
  if (rest.length) query.f = JSON.stringify(rest);
  return query;
}

function fallbackNav(schema: AppSchema, links: NavLinks, fallback: string): WorkspaceNav {
  const models = schema.models.filter(model => !model.child_only) as unknown as ScreenModel[];
  const reports = schema.reports ?? [];
  const workspaces = appsOf(schema, fallback).map(({ key, label }): Workspace => {
    const mine = models.filter(model => appOf(model) === key);
    const sections: NavSection[] = ['Documents', 'Records'].map(group => ({
      key: group.toLowerCase(), label: group, icon: SECTION_ICONS[group],
      items: mine.filter(model => groupOf(model) === group).map(model => {
        return { key: `model:${model.name}`, label: model.plural, href: links.list(model.name), kind: 'list' as const, icon: KIND_ICONS.list, model: model.name };
      }),
    })).filter(section => section.items.length);
    const own = reports.filter(report => reportApp(schema, report) === key);
    if (own.length) sections.push({ key: 'reports', label: 'Reports', icon: KIND_ICONS.report,
      items: own.map(report => ({ key: `report:${report.name}`, label: report.label, href: links.report(report.name), kind: 'report' as const, icon: KIND_ICONS.report })) });
    return { key: key || 'app', label, icon: SHELL_ICONS.workspace, tint: tintFor(key || label), sections };
  });
  return { workspaces: workspaces.filter(workspace => workspace.sections.length) };
}

/** The address parts that pick out a shortcut among others of the same page: the view, the list tab and the filters. */
const MATCHED = ['view', 'tab', 'f'];
const pathOf = (href: string) => href.split(/[?#]/)[0].replace(/\/$/, '') || '/';
const queryOf = (href: string) => new URLSearchParams(href.includes('?') ? href.slice(href.indexOf('?') + 1).split('#')[0] : '');
/** A query value compared by meaning: ?f= as parsed JSON, so spacing and escaping don't matter. */
function same(key: string, a: string | null, b: string | null): boolean {
  if (a === b) return true;
  if (key !== 'f' || a === null || b === null) return false;
  try { return JSON.stringify(JSON.parse(a)) === JSON.stringify(JSON.parse(b)); } catch { return false; }
}

/** Two queries with the same parts and values, in any order. */
function sameQuery(a: URLSearchParams, b: URLSearchParams): boolean {
  const keys = new Set([...a.keys(), ...b.keys()]);
  return [...keys].every(key => same(key, a.get(key), b.get(key)));
}

/**
 * The shortcut an address belongs to, if any. Its path must equal the shortcut's, or be a page under it (a
 * record of the list). On the page itself, every view, tab and filter the shortcut names must be in the address,
 * and the shortcut naming the most of them wins: on /m/task?f=[...] the "Open tasks" shortcut beats "Tasks",
 * which still matches any other filter. Under it (a record), the plainest shortcut wins. Slips never match. On a
 * tie, a shortcut of `prefer` (the workspace shown) wins, so a page two workspaces share keeps the sidebar as it is.
 * A link (a URL item) matches only its own address, every query part included: "All reports" at /?explorer=reports
 * is not marked on /?explorer=data or on any other page of the root path.
 */
export function activeShortcut(nav: WorkspaceNav, path: string, prefer?: string): { workspace: Workspace; section: NavSection; item: NavShortcut } | null {
  let best: { workspace: Workspace; section: NavSection; item: NavShortcut } | null = null, score = -Infinity;
  const bare = pathOf(path), query = queryOf(path);
  for (const workspace of nav.workspaces) for (const section of workspace.sections) for (const item of section.items) {
    if (item.slip || item.external || !item.href) continue;
    const target = pathOf(item.href), own = queryOf(item.href);
    const named = MATCHED.filter(key => own.has(key));
    let mine: number;
    if (item.kind === 'link') {
      if (bare !== target || !sameQuery(own, query)) continue;
      mine = target.length * 100 + [...own.keys()].length;
    } else if (bare === target) {
      if (!named.every(key => same(key, own.get(key), query.get(key) ?? item.defaults?.[key] ?? null))) continue;
      mine = target.length * 100 + named.length;
    } else if (bare.startsWith(target + '/')) mine = target.length * 100 - named.length - 50;
    else continue;
    if (workspace.key === prefer) mine += 0.5;
    if (mine > score) { best = { workspace, section, item }; score = mine; }
  }
  return best;
}

// The workspaces in /api/schema change when someone saves a workspace override: the editor reads the
// schema again and hands the new list here, and the shell rebuilds the sidebar from it.
const versions = new WeakMap<AppContext, Cell<number>>();
export function workspacesVersion(ctx: AppContext): Cell<number> {
  let found = versions.get(ctx);
  if (!found) versions.set(ctx, found = cell(0, { name: 'nav.workspaces version' }));
  return found;
}
export function replaceWorkspaces(ctx: AppContext, workspaces: WorkspaceSchema[] | undefined) {
  ctx.schema.schema.workspaces = workspaces;
  const version = workspacesVersion(ctx);
  version.set(version.peek() + 1);
}

// Record counts beside models, fetched by the shell when the person turned them on in Settings.
// The Explorer reads the same numbers and never asks for its own.
const counts = new WeakMap<AppContext, Cell<Record<string, number>>>();
export function modelCounts(ctx: AppContext): Cell<Record<string, number>> {
  let found = counts.get(ctx);
  if (!found) counts.set(ctx, found = cell<Record<string, number>>({}, { name: 'nav.counts' }));
  return found;
}
