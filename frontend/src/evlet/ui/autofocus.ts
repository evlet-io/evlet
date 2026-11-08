// Autofocus for forms that start empty: a new record (full page or side panel) and a slip. The first
// field that can take a value gets the focus, so typing can start at once. initialFocusField() is the
// one place that chooses it; autofocusForm() focuses it once the form shows, and only on a device with
// a mouse or trackpad (on a phone the focus would open the on-screen keyboard), and only when the person
// has not moved the focus somewhere else while the form loaded. An existing record never calls it.

import { untrack } from '../reactive';
import { keyboardFirst } from './core';
import type { FieldLike } from './fields';

/** One section of a form in screen order: its fields, and whether it is open (a closed section is skipped). */
export interface FocusSection { fields: FieldLike[]; open?: boolean }

/**
 * The name of the field a new form focuses first, or null for none: the first field, in screen order,
 * that shows and can be edited. Read-only, calculated, hidden and locked fields are skipped, and so are
 * the fields of a closed section. `model` is where a model's own choice will plug in; nothing is read
 * from it yet.
 */
export function initialFocusField(model: { name: string }, layout: FocusSection[]): string | null {
  void model;
  for (const section of layout) {
    if (section.open === false) continue;
    const found = section.fields.find(takesFocus);
    if (found) return found.meta.name;
  }
  return null;
}

/**
 * A form's sections in the order a declared record layout shows them on the tab a new record opens on:
 * `shown` is the section labels of that tab (or of the layout's blocks without Tabs), in order. Sections it
 * does not name are left out; without a layout, or when it names none of them, the form's own order stays.
 */
export function layoutFocusOrder<S extends { label: string }>(sections: readonly S[], shown: readonly string[] | null): S[] {
  if (!shown) return [...sections];
  const ordered = shown.flatMap(label => sections.filter(section => section.label === label));
  return ordered.length ? ordered : [...sections];
}

function takesFocus(field: FieldLike): boolean {
  const meta = field.meta;
  if (meta.hidden || meta.read_only || field.calculated || (meta.compute !== null && meta.compute !== undefined)) return false;
  return untrack(() => field.editable.get() && field.rules.get().applies);
}

// The control that takes typing inside a field block. Never the ? tooltip or a rich text toolbar button.
const CONTROL = 'input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled), [contenteditable="true"], '
  + 'button.in-select:not(:disabled), button.in-link:not(:disabled), button[role=switch]:not(:disabled), .in-multi button:not(:disabled)';

/**
 * Focus a form's first field (initialFocusField) once `root` is on the page. Nothing happens on a touch
 * device, when no field qualifies, or when the focus moved since this was called: only the page itself,
 * `before` (by default the element focused at the call: pass what held it when the form was asked for,
 * before it loaded), or a container of the form (the side panel or the dialog) may hold it. Focusing
 * changes no value. Returns a function that cancels a pending focus.
 */
export function autofocusForm(root: HTMLElement, choose: () => string | null, before: Element | null = document.activeElement): () => void {
  let cancelled = false, frames = 0;
  const free = () => {
    const now = document.activeElement;
    return !now || now === document.body || now === document.documentElement || now === before || now === root || now.contains(root);
  };
  const attempt = () => {
    if (cancelled) return;
    // A page is drawn before the router puts it on screen: wait a few frames for that.
    if (!root.isConnected) { if (++frames < 10) requestAnimationFrame(attempt); return; }
    if (!keyboardFirst() || !free()) return;
    const name = choose();
    if (!name) return;
    const block = root.querySelector<HTMLElement>(`[data-field="${CSS.escape(name)}"]`);
    if (!block || block.closest('[hidden],[inert]')) return;
    block.querySelector<HTMLElement>(CONTROL)?.focus();
  };
  queueMicrotask(attempt);
  return () => { cancelled = true; };
}
