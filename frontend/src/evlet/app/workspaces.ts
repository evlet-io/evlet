// Workspaces: what the sidebar shows, three levels deep (workspace > section > shortcut), and the
// apps the Explorer groups reports and models by.
//
// workspaceNav(schema, links) is the one place the sidebar's structure comes from. Until apps declare
// workspaces in /api/schema it builds the fallback: one workspace per installed app (the app part of
// each model's resource id, such as "showcase.Project"), with the sidebar's earlier groups as sections
// (Documents, Records) and a Reports section when the app has reports. A declared workspace list
// can replace fallbackNav() here without changing the shell, which only reads the returned shape.

import { cell, type Cell } from '../reactive';
import type { AppSchema, ModelSchema, ReportSchema } from '../model/schema';
import { tintFor, type Tint } from '../ui/core';
import { groupOf, identity, type ScreenModel } from './meta';
import type { AppContext } from './context';

export interface NavShortcut { key: string; label: string; href: string; icon?: string; tint?: Tint }
export interface NavSection { key: string; label: string; icon: string; count?: number; items: NavShortcut[] }
export interface Workspace { key: string; label: string; icon: string; tint?: Tint; sections: NavSection[] }
export interface WorkspaceNav { workspaces: Workspace[] }

/** How a shortcut's address is made: the shell passes the router's. */
export interface NavLinks { list(model: string): string; report(name: string): string }

const REPORT_ICON = 'chart-column';
const SECTION_ICONS: Record<string, string> = { Documents: 'file-text', Records: 'table-2', Reports: REPORT_ICON };

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

/** The sidebar's structure. fallback names the app of models without a resource id (the application's name). */
export function workspaceNav(schema: AppSchema, links: NavLinks, fallback = 'Workspace'): WorkspaceNav {
  // Declared workspaces (once /api/schema carries them) map to the same shape here.
  return fallbackNav(schema, links, fallback);
}

function fallbackNav(schema: AppSchema, links: NavLinks, fallback: string): WorkspaceNav {
  const models = schema.models.filter(model => !model.child_only) as unknown as ScreenModel[];
  const reports = schema.reports ?? [];
  const workspaces = appsOf(schema, fallback).map(({ key, label }): Workspace => {
    const mine = models.filter(model => appOf(model) === key);
    const sections: NavSection[] = ['Documents', 'Records'].map(group => ({
      key: group.toLowerCase(), label: group, icon: SECTION_ICONS[group],
      items: mine.filter(model => groupOf(model) === group).map(model => {
        const { icon, tint } = identity(model);
        return { key: `model:${model.name}`, label: model.plural, href: links.list(model.name), icon, tint };
      }),
    })).filter(section => section.items.length);
    const own = reports.filter(report => reportApp(schema, report) === key);
    if (own.length) sections.push({ key: 'reports', label: 'Reports', icon: REPORT_ICON,
      items: own.map(report => ({ key: `report:${report.name}`, label: report.label, href: links.report(report.name), icon: REPORT_ICON, tint: 'rose' as Tint })) });
    return { key: key || 'app', label, icon: 'layers', tint: tintFor(key || label), sections };
  });
  return { workspaces: workspaces.filter(workspace => workspace.sections.length) };
}

/** The shortcut a path opens, if any: the longest shortcut path that equals the path or contains it. */
export function activeShortcut(nav: WorkspaceNav, path: string): { workspace: Workspace; section: NavSection; item: NavShortcut } | null {
  let best: { workspace: Workspace; section: NavSection; item: NavShortcut } | null = null, length = -1;
  const bare = path.split('?')[0].replace(/\/$/, '') || '/';
  for (const workspace of nav.workspaces) for (const section of workspace.sections) for (const item of section.items) {
    const target = item.href.split('?')[0].replace(/\/$/, '') || '/';
    if ((bare === target || bare.startsWith(target + '/')) && target.length > length) { best = { workspace, section, item }; length = target.length; }
  }
  return best;
}

// Record counts beside models, fetched by the shell when the person turned them on in Settings.
// The Explorer reads the same numbers and never asks for its own.
const counts = new WeakMap<AppContext, Cell<Record<string, number>>>();
export function modelCounts(ctx: AppContext): Cell<Record<string, number>> {
  let found = counts.get(ctx);
  if (!found) counts.set(ctx, found = cell<Record<string, number>>({}, { name: 'nav.counts' }));
  return found;
}
