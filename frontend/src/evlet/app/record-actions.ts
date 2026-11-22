// What a record page decides before it runs anything (docs/ui.md, Compact record page): whether a move or an action
// can run now and why not, which actions the role has, whether the record can be deleted, and what More lists. Plain
// functions of the schema and the detail's extras, with no DOM; app/record-page.ts wires them to the store and the
// header.

import { can } from '../model/schema';
import type { MenuItem } from '../ui/layers';
import { formatKeys } from '../ui/shortcuts';
import { stepLabel, type ActionMeta, type DetailExtras, type DetailMove, type ScreenModel } from './meta';
import { SCREEN_KEYS } from './shortcuts';

/** Why nothing runs on this record now: a new one is not saved yet, an edited one has unsaved changes. */
export function editBlocked(isNew: boolean, dirty: () => boolean): string | null {
  if (isNew) return 'Save the record first';
  if (dirty()) return 'Save or discard your changes first';
  return null;
}

/** One move per target state: the detail lists the rules out of the current state, the allowed one first. */
export function moveTo(extras: DetailExtras, to: string): DetailMove | undefined {
  const moves = extras._moves ?? [];
  return moves.find(item => item.to === to && item.allowed) ?? moves.find(item => item.to === to);
}

/** Why the move to `to` cannot run (from the state `from` reads), or null when it can. */
export function moveBlocked(model: ScreenModel, extras: DetailExtras, to: string, from: () => string): string | null {
  const move = moveTo(extras, to);
  if (!move) return `This record cannot move from ${stepLabel(model, from())} to ${stepLabel(model, to)}`;
  return move.allowed ? null : move.reason ?? `${move.label} is not available now`;
}

/** The actions of the record: those the detail lists for this role; without that list, the schema's. */
export function recordActions(model: ScreenModel, extras: DetailExtras): ActionMeta[] {
  const declared = model.actions ?? [];
  const allowed = extras._actions;
  return allowed ? allowed.map(item => ({ ...declared.find(action => action.name === item.name), name: item.name, label: item.label })) : declared;
}

/** Why the detail refuses this action now, or null. */
export function actionRefused(extras: DetailExtras, action: ActionMeta): string | null {
  const entry = extras._actions?.find(item => item.name === action.name);
  return entry && !entry.allowed ? entry.reason ?? `${action.label} is not available now` : null;
}

/** A saved record the role may delete, unlocked; a record that ever posted is kept (its entries name it). */
export function deletable(model: ScreenModel, isNew: boolean, locked: boolean, extras: DetailExtras): boolean {
  return !isNew && can(model, 'delete') && !locked && extras._deletable !== false;
}

/** The record's own items at the end of More. */
export interface RecordMenu {
  isNew: boolean;
  duplicate: boolean;
  /** "Reset layout", enabled while the person changed which sections are open. */
  layout: { changed: boolean } | null;
  remove: boolean;
  copyLink(): void;
  copyId(): void;
  reload(): void;
  onDuplicate(): void;
  resetLayout(): void;
  onRemove(): void;
}

/** Copy link; on a saved record Copy record ID and Reload; Duplicate, Reset layout and Delete when they apply. */
export function recordMenuItems(menu: RecordMenu): MenuItem[] {
  const items: MenuItem[] = [{ label: 'Copy link', icon: 'link', onSelect: menu.copyLink }];
  if (!menu.isNew) items.push({ label: 'Copy record ID', icon: 'copy', onSelect: menu.copyId },
    { label: 'Reload', icon: 'rotate-ccw', onSelect: menu.reload });
  if (menu.duplicate) items.push({ label: 'Duplicate', icon: 'copy', hint: formatKeys(SCREEN_KEYS.duplicate), onSelect: menu.onDuplicate });
  if (menu.layout) items.push({ label: 'Reset layout', icon: 'layout-grid', disabled: !menu.layout.changed, onSelect: menu.resetLayout });
  if (menu.remove) items.push('separator', { label: 'Delete', icon: 'trash-2', danger: true, hint: formatKeys(SCREEN_KEYS.remove), onSelect: menu.onRemove });
  return items;
}
