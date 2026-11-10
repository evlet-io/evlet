// The navigation icons (docs/ui.md#navigation-icons): one icon per kind of shortcut, and one per main
// element of the shell. A shortcut's icon comes from what it opens, never from the item, so every
// list looks alike in the sidebar, the workspace editor and the Explorer. Every name is in ui/icon-data.ts.

import type { WorkspaceItem } from '../model/schema';
import type { Tint } from '../ui/core';

/** What a shortcut opens. A filtered list is a list. */
export type ShortcutKind = 'list' | 'board' | 'report' | 'dashboard' | 'slip' | 'link';

export const KIND_ICONS: Readonly<Record<ShortcutKind, string>> = {
  list: 'list',
  board: 'square-kanban',
  report: 'chart-column',
  dashboard: 'activity',
  slip: 'sticky-note',
  link: 'link',
};

/** The tile colour of each kind, where a kind shows as a tile (Explorer cards, palette rows). */
export const KIND_TINTS: Readonly<Record<ShortcutKind, Tint>> = {
  list: 'blue', board: 'violet', report: 'rose', dashboard: 'amber', slip: 'teal', link: 'slate',
};

/** The shell's main elements. */
export const SHELL_ICONS = {
  docs: 'file-text',
  eva: 'sparkles',
  explorer: 'layout-grid',
  search: 'search',
  history: 'history',
  notifications: 'bell',
  help: 'circle-help',
  settings: 'settings',
  controls: 'sliders-horizontal',
  account: 'user',
  workspace: 'layers',
  customize: 'pencil',
  menu: 'menu',
} as const;

/** The Explorer's views. */
export const EXPLORER_ICONS = {
  reports: KIND_ICONS.report,
  ledger: 'receipt',
  data: 'table-2',
  dashboards: KIND_ICONS.dashboard,
} as const;

/** The kind of a declared item, or of a shortcut the workspace editor adds. */
export function kindOf(item: { kind?: WorkspaceItem['kind']; model?: string; view?: string; report?: string; slip?: string; url?: string }): ShortcutKind {
  if (item.kind === 'report' || item.report) return 'report';
  if (item.kind === 'slip' || item.slip) return 'slip';
  if (item.kind === 'url' || item.url) return 'link';
  return item.view === 'board' ? 'board' : 'list';
}

/** The icon of a kind. */
export const kindIcon = (kind: ShortcutKind) => KIND_ICONS[kind];
