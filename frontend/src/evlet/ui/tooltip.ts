// Tooltip: one dark label for the whole page, shown at once on hover or keyboard focus and
// kept until the pointer leaves. Above the target pointing down, below when there is no room
// above, or to the right (collapsed sidebar). Never on touch-only devices. Hidden on scroll and
// when the target leaves the page. While shown, the target is described by it for screen readers.

import { effect, onCleanup } from '../reactive';
import { canHover, read, type Prop } from './core';

export type Placement = 'auto' | 'right';
let tip: HTMLElement | null = null;
let owner: Element | null = null;

function host(): HTMLElement {
  if (tip && tip.isConnected) return tip;
  tip = document.createElement('div');
  tip.className = 'tip';
  tip.id = 'evlet-tip';
  tip.setAttribute('role', 'tooltip');
  document.body.append(tip);
  return tip;
}

/** Show the tooltip for a target now. */
export function showTip(target: Element, text: string, placement: Placement = 'auto') {
  if (!text) { hideTip(); return; }
  const element = host();
  owner = target;
  element.textContent = text;
  element.className = 'tip';
  element.style.left = '0px';
  element.style.top = '0px';
  const box = target.getBoundingClientRect(), width = element.offsetWidth, height = element.offsetHeight;
  let x: number, y: number, place: string;
  if (placement === 'right') { x = box.right + 10; y = box.top + box.height / 2 - height / 2; place = 'right'; }
  else {
    x = box.left + box.width / 2 - width / 2; y = box.top - height - 10; place = 'above';
    if (y < 8) { y = box.bottom + 10; place = 'below'; }
  }
  x = Math.max(8, Math.min(x, window.innerWidth - width - 8));
  element.style.left = `${x}px`;
  element.style.top = `${y}px`;
  element.style.setProperty('--ax', `${place === 'right' ? 0 : Math.max(10, Math.min(width - 10, box.left + box.width / 2 - x))}px`);
  element.classList.add(place, 'show');
  const described = (target.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean);
  if (!described.includes(element.id)) target.setAttribute('aria-describedby', [...described, element.id].join(' '));
}

/** Hide the tooltip (for a given target only, when one is passed). */
export function hideTip(target?: Element) {
  if (!tip || (target && owner !== target)) return;
  tip.classList.remove('show');
  if (owner) {
    const rest = (owner.getAttribute('aria-describedby') ?? '').split(' ').filter(id => id && id !== tip!.id);
    if (rest.length) owner.setAttribute('aria-describedby', rest.join(' ')); else owner.removeAttribute('aria-describedby');
  }
  owner = null;
}

/** The text currently shown, for tests and debugging. */
export const tipText = () => tip?.classList.contains('show') ? tip.textContent : null;

let watching = false;
function watchScroll() {
  if (watching) return;
  watching = true;
  document.addEventListener('scroll', () => hideTip(), { capture: true, passive: true });
}

/**
 * Give an element a tooltip. The text may be reactive; an empty text shows nothing. The
 * listeners are removed with the current scope, and the tooltip hides if the element goes.
 */
export function tooltip(target: HTMLElement, text: Prop<string | null | undefined>, placement: Placement = 'auto') {
  watchScroll();
  const current = () => read(text) ?? '';
  const show = (event: Event) => {
    if (event.type === 'mouseenter' && !canHover()) return;
    showTip(target, current(), placement);
  };
  const hide = () => hideTip(target);
  for (const type of ['mouseenter', 'focus']) target.addEventListener(type, show);
  for (const type of ['mouseleave', 'blur', 'pointerdown']) target.addEventListener(type, hide);
  // Keep a shown tooltip in step with its text.
  effect(() => { const value = current(); if (owner === target) { if (value) showTip(target, value, placement); else hideTip(target); } });
  onCleanup(() => {
    for (const type of ['mouseenter', 'focus']) target.removeEventListener(type, show);
    for (const type of ['mouseleave', 'blur', 'pointerdown']) target.removeEventListener(type, hide);
    hideTip(target);
  });
  return target;
}

/** Tooltip markup as a static element for the Library (no positioning). */
export function TooltipSample(text: string, place: 'above' | 'below' = 'above') {
  const element = document.createElement('span');
  element.className = `tip-demo ${place}`;
  element.textContent = text;
  return element;
}
