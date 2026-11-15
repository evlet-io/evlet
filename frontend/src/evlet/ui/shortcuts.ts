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

/** Commit what is typed in the focused input before a shortcut saves (fields commit on change). */
export function commitTyped(): boolean {
  const active = document.activeElement;
  if ((active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) && isTyping(active)) active.dispatchEvent(new Event('change', { bubbles: true }));
  return active?.getAttribute('aria-invalid') !== 'true';
}
