// Screen shortcuts on lists and record forms (docs/ui.md#keyboard-shortcuts): Mod+S saves,
// Mod+Shift+D deletes (or discards a new record), Alt+N opens a new record and Alt+Shift+D
// duplicates one. A screen binds the actions it has with useShortcuts(); one document listener
// runs the action of the screen that holds the focus. A side panel's record holds it while the
// focus is inside the panel; otherwise the list does. Nothing fires while a dialog is open: the
// dialog has its own keys (ui/layers.ts).

import { html } from '../dom';
import { onCleanup } from '../reactive';
import { Key } from '../ui/base';
import { layersOpen, openModal } from '../ui/layers';
import { formatKeys, isMac, keyMatches } from '../ui/shortcuts';

/** The shortcuts a screen can bind, with their keys. */
export const SCREEN_KEYS = {
  save: 'Mod+S',
  remove: 'Mod+Shift+D',
  create: 'Alt+N',
  duplicate: 'Alt+Shift+D',
} as const;
export type ScreenAction = keyof typeof SCREEN_KEYS;

export interface ShortcutAction {
  /** What the help sheet says it does: "Save", "Delete this customer". */
  label: string;
  run: () => void;
}

export interface ShortcutScope {
  /** The screen's element: its shortcuts act while the focus is inside it. */
  root: HTMLElement;
  /** A side panel inside another screen: it acts only while the focus is inside it, never by default. */
  inner?: boolean;
  /** Where the focus counts, when that is wider than root (the side panel around a record). */
  area?: () => HTMLElement | null;
  /** The actions available now (read on each key press, so a lock or a permission change is followed). */
  actions: () => Partial<Record<ScreenAction, ShortcutAction | null>>;
  /** Other keys the screen answers itself, for the help sheet only: [combination, what it does]. */
  help?: () => Array<[combo: string, what: string]>;
}

const scopes: ShortcutScope[] = [];
let listening = false;

/** Bind a screen's shortcuts for the life of the current scope. */
export function useShortcuts(scope: ShortcutScope) {
  scopes.push(scope);
  if (!listening) { listening = true; document.addEventListener('keydown', onKey); }
  onCleanup(() => { const at = scopes.indexOf(scope); if (at >= 0) scopes.splice(at, 1); });
}

/** The screens that answer a key, nearest first: those holding the focus, then the page itself. */
function chain(): ShortcutScope[] {
  const live = scopes.filter(scope => scope.root.isConnected);
  const active = document.activeElement;
  const holding = live.filter(scope => active && (scope.area?.() ?? scope.root).contains(active)).reverse();
  const page = live.filter(scope => !scope.inner && !holding.includes(scope)).reverse();
  return [...holding, ...page];
}

/** The action a screen shortcut runs now, if any. */
export function screenAction(name: ScreenAction): ShortcutAction | null {
  for (const scope of chain()) {
    const action = scope.actions()[name];
    if (action) return action;
  }
  return null;
}

const dialogOpen = () => layersOpen.peek() > 0 || Boolean(document.querySelector('.modal-wrap.show,.drawer.show'));

function onKey(event: KeyboardEvent) {
  if (event.defaultPrevented) return;
  const name = (Object.keys(SCREEN_KEYS) as ScreenAction[]).find(key => keyMatches(event, SCREEN_KEYS[key]));
  if (!name) return;
  // Mod+S never opens the browser's Save Page, even where nothing saves.
  if (name === 'save') event.preventDefault();
  // A dialog answers its own keys; the screen under it does not.
  if (dialogOpen()) { if (name === 'remove') event.preventDefault(); return; }
  const action = screenAction(name);
  if (!action) return;
  event.preventDefault();
  // A held key saves, deletes or opens a record once.
  if (event.repeat) return;
  action.run();
}

/** The current screen's shortcuts, for the help sheet. */
export function screenShortcuts(): Array<[keys: string, what: string]> {
  const actions = (Object.keys(SCREEN_KEYS) as ScreenAction[]).flatMap(name => {
    const action = screenAction(name);
    return action ? [[formatKeys(SCREEN_KEYS[name]), action.label] as [string, string]] : [];
  });
  const help = chain().flatMap(scope => scope.help?.() ?? []).map(([combo, what]) => [formatKeys(combo), what] as [string, string]);
  return [...actions, ...help];
}

/** What every dialog answers (ui/layers.ts). */
const DIALOG_KEYS = (): Array<[string, string]> => [
  [formatKeys('Mod+S'), 'Confirm, save or submit'], [formatKeys('Mod+Shift+D'), 'Cancel'],
  ['← or →', 'Move between the actions'], [formatKeys('Enter'), 'Press the focused action'], ['Esc', 'Close'],
];

/** A row the shell lists ("⌘K or Ctrl K") as this platform writes it. */
function platformKeys(keys: string): string {
  const parts = keys.split(' or ');
  const mine = parts.find(part => isMac() === part.includes('⌘')) ?? parts[0];
  return isMac() ? mine : mine.replace(/⌘\s?/g, 'Ctrl ');
}

/**
 * The shortcut sheet ("?"): the current screen's shortcuts first, then those that work everywhere
 * (`everywhere`, from the shell), each in this platform's keys.
 */
export function openShortcutSheet(everywhere: Array<[keys: string, what: string]>) {
  const here = screenShortcuts();
  const rows = (items: Array<[string, string]>) => items.map(([keys, what]) => html`<dt>${keys.split(/ (then|or) /).map(part => part === 'then' || part === 'or' ? ` ${part} ` : Key(part))}</dt><dd>${what}</dd>`);
  const group = (title: string, items: Array<[string, string]>) => items.length ? html`<section class="ev-keys-g"><h3 class="ev-keys-h">${title}</h3><dl class="ev-keys">${rows(items)}</dl></section>` : null;
  return openModal({ title: 'Keyboard shortcuts', icon: 'command', tint: 'slate', narrow: true, className: 'ev-keys-sheet',
    body: () => [group('This screen', here), group('Everywhere', everywhere.map(([keys, what]) => [platformKeys(keys), what])), group('In a dialog', DIALOG_KEYS())] });
}
