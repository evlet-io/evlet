// Small helpers shared by the components.
//
// A property is a plain value, an accessor function, or a cell/derive (`Prop<T>`). Components
// read properties with `read()` inside bindings, so a plain value is set once and a reactive
// one updates only the node bound to it.

import { read } from '../dom';
import { effect, type Readable } from '../reactive';

export { read };
export type Prop<T> = T | (() => T) | Readable<T>;
export type Tint = 'blue' | 'green' | 'amber' | 'rose' | 'violet' | 'teal' | 'slate';
export const TINTS: Tint[] = ['blue', 'green', 'amber', 'rose', 'violet', 'teal', 'slate'];

/** A tint name from data, or slate when it is not one of the seven. Keeps class names safe. */
export const tint = (value: unknown): Tint => TINTS.includes(value as Tint) ? value as Tint : 'slate';

let ids = 0;
/** A unique element id for labels and aria references. */
export const uid = (prefix = 'ev') => `${prefix}-${++ids}`;

/** Whether the device has a pointer that hovers (tooltips are never shown on touch-only devices). */
export function canHover(): boolean {
  try { return !window.matchMedia?.('(hover: none)')?.matches; } catch { return true; }
}

/** The first and last focusable elements inside a container. */
export function focusables(root: Element): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(element => !element.closest('[hidden]') && !element.closest('[inert]'));
}

/** Keep Tab and Shift Tab inside a dialog. */
export function trapFocus(root: HTMLElement, event: KeyboardEvent) {
  if (event.key !== 'Tab') return;
  const items = focusables(root);
  if (!items.length) { event.preventDefault(); root.focus(); return; }
  const first = items[0], last = items[items.length - 1], active = document.activeElement;
  if (event.shiftKey && (active === first || active === root)) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
}

/** Initials for an avatar: "Aslam Kaithakath" -> "AK". */
export function initials(name: string): string {
  const parts = String(name ?? '').trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase() || '?';
}

/** A stable tint for a name, so the same person or company always gets the same colour. */
export function tintFor(name: string): Tint {
  let hash = 0;
  for (const char of String(name ?? '')) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TINTS[hash % TINTS.length];
}

/** Compact money for tiles and board columns: 186K, 1.15M. */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e6) return `${+(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${+(value / 1e3).toFixed(abs >= 1e5 ? 0 : 1)}K`;
  return String(Math.round(value));
}

/** A text node bound to an accessor: for branches of when() that show changing text. */
export function textOf(value: () => string): Text {
  const node = document.createTextNode('');
  effect(() => { const next = value(); if (node.data !== next) node.data = next; });
  return node;
}
