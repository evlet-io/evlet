// Keyboard shortcut primitives: the platform's command key, matching a key press against a
// combination written once ('Mod+S', 'Mod+Shift+D', 'Alt+N', '?'), and showing it the way the
// platform writes it (⌘S on a Mac, Ctrl+S elsewhere). Screens bind their shortcuts in
// app/shortcuts.ts; dialogs use these for Save and Cancel (ui/layers.ts).

/** True on macOS and iOS, where Mod is ⌘; elsewhere Mod is Ctrl. Read on each call, so tests can stub it. */
export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || navigator.userAgent;
  return /Mac|iPhone|iPad|iPod/i.test(platform);
}

interface Combo { mod: boolean; shift: boolean; alt: boolean; key: string }

function parse(combo: string): Combo {
  const parts = combo.split('+');
  // A combination that ends in "+" names the plus key itself.
  const key = combo.endsWith('++') ? '+' : parts.pop()!;
  const has = (name: string) => parts.includes(name);
  return { mod: has('Mod'), shift: has('Shift'), alt: has('Alt'), key };
}

/**
 * Whether a key press is the combination. Mod is ⌘ on a Mac and Ctrl elsewhere, and the other one
 * must not be held. A letter matches the key typed or the physical key (event.code), so Option+N
 * on a Mac, which types a dead key, still reads as Alt+N. Symbols (?, /) match the character typed,
 * whatever Shift it took.
 */
export function keyMatches(event: KeyboardEvent, combo: string): boolean {
  const want = parse(combo), mac = isMac();
  const mod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  if (want.mod !== mod || (!want.mod && (event.metaKey || event.ctrlKey))) return false;
  if (want.alt !== event.altKey) return false;
  if (/^[A-Z]$/.test(want.key)) {
    if (want.shift !== event.shiftKey) return false;
    return event.key.toLowerCase() === want.key.toLowerCase() || event.code === `Key${want.key}`;
  }
  if (want.key === 'Esc') return event.key === 'Escape';
  if (want.key.length > 1 && want.shift !== event.shiftKey) return false;
  return event.key === want.key;
}

const MAC_SYMBOLS: Record<string, string> = { Mod: '⌘', Shift: '⇧', Alt: '⌥', Enter: '↩', Esc: 'Esc' };

/** A combination as the platform writes it: ⇧⌘D and ⌥N on a Mac, Ctrl+Shift+D and Alt+N elsewhere. */
export function formatKeys(combo: string): string {
  const { mod, shift, alt, key } = parse(combo);
  if (isMac()) {
    // Apple's order: Option, Shift, Command, then the key.
    const symbols = [alt ? MAC_SYMBOLS.Alt : '', shift ? MAC_SYMBOLS.Shift : '', mod ? MAC_SYMBOLS.Mod : ''].join('');
    return symbols + (symbols && key.length > 1 ? ' ' : '') + (MAC_SYMBOLS[key] ?? key);
  }
  return [mod ? 'Ctrl' : '', alt ? 'Alt' : '', shift ? 'Shift' : '', key].filter(Boolean).join('+');
}

/** The combination for aria-keyshortcuts: Meta+S on a Mac, Control+S elsewhere. */
export function ariaKeys(combo: string): string {
  const { mod, shift, alt, key } = parse(combo);
  return [mod ? (isMac() ? 'Meta' : 'Control') : '', alt ? 'Alt' : '', shift ? 'Shift' : '', key === 'Esc' ? 'Escape' : key].filter(Boolean).join('+');
}

/** A tooltip with its shortcut, as the app writes them: "Save  ⌘S". */
export const withShortcut = (label: string, combo: string) => `${label}  ${formatKeys(combo)}`;

/** Whether the element takes typing: plain letter shortcuts stay out of the way there. */
export function isTyping(element: Element | null = document.activeElement): boolean {
  if (!element) return false;
  const html = element as HTMLElement;
  if (html.isContentEditable || html.closest?.('[contenteditable="true"]')) return true;
  if (element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') return true;
  if (element.tagName !== 'INPUT') return false;
  return !['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color', 'file'].includes((element as HTMLInputElement).type);
}

const committers = new WeakMap<Element, () => boolean | undefined>();

/**
 * Let a control that commits by hand (commitOn: 'manual') commit when a shortcut saves while the
 * focus is inside `host`: `commit` writes what is being edited, as leaving it would, and returns
 * false when it could not be read (the control then shows the error and keeps the focus), or
 * undefined when nothing is being edited there, so the focused input commits as usual.
 */
export function commitWithin(host: Element, commit: () => boolean | undefined) { committers.set(host, commit); }

/**
 * Commit what is typed in the focused input before a shortcut saves (fields commit on change).
 * False only when that commit failed: a field's change handler cancels the change event when the
 * typed text could not be read or broke a rule (ui/fields.ts), so the field now shows that error.
 * A control that commits by hand registers itself (commitWithin) and is committed through that.
 * Any other focused control (a select, tags, an image, a link picker) is not committed here, and a stale error it shows (one the server sent) does not block the save:
 * as with the Save button, the save runs and the server answers again.
 */
export function commitTyped(): boolean {
  const active = document.activeElement;
  // A control that commits by hand (a child table's cell, an inline value) commits as leaving it would.
  for (let at: Element | null = active; at; at = at.parentElement) {
    const done = committers.get(at)?.();
    if (done !== undefined) return done;
  }
  if (!((active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) && isTyping(active))) return true;
  return active.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
}
