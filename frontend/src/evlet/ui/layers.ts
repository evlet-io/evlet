// Floating layers: Popover (with Menu and MenuButton), Modal, Drawer and Toast.
//
// Each layer renders in its own scope on document.body and is removed, with its effects and
// listeners, when it closes. Esc closes the top layer; dialogs keep focus inside, focus their
// first field (or, with no field, their main action) and give focus back to what opened them.
// In a dialog the arrow keys move between the footer's actions, Mod+S runs the main action and
// Mod+Shift+D cancels (docs/ui.md#keyboard-shortcuts). No browser dialogs are used.

import { html, type Child } from '../dom';
import { cell, runWithOwner, scope } from '../reactive';
import { Button, Icon, type ButtonProps } from './base';
import { focusables, keyboardFirst, read, tint, trapFocus, uid, type Prop, type Tint } from './core';
import { hideTip } from './tooltip';
import { commitTyped, keyMatches } from './shortcuts';

// Render into a fresh root scope (not owned by whatever opened the layer).
function layer(render: (dispose: () => void) => Node): { node: Node; dispose: () => void } {
  let node!: Node;
  const dispose = runWithOwner(null, () => scope(stop => { node = render(stop); }));
  return { node, dispose };
}

// ---------- popover ----------

let current: { anchor: Element; close: (restore?: boolean) => void } | null = null;

export interface PopoverOptions {
  className?: string;
  /** role of the panel: menu (default) or dialog. */
  role?: 'menu' | 'dialog';
  label?: string;
  /** Align the panel's left edge with the anchor (auto picks the side that fits), or place it beside
   * the anchor on its right, top edges level (side); side falls back to below when it does not fit. */
  align?: 'auto' | 'left' | 'side';
  onClose?: () => void;
}

/**
 * Open a floating panel anchored to its button. Opening it again from the same anchor closes
 * it. Only one is open at a time. It flips above near the bottom edge and closes on outside
 * click, Esc or closePopover() (for example on route change). Returns close.
 */
export function openPopover(anchor: HTMLElement, render: (close: () => void) => Child, options: PopoverOptions = {}): () => void {
  hideTip();
  if (current?.anchor === anchor) { current.close(true); return () => {}; }
  current?.close(false);
  let closed = false;
  const close = (restore = true) => {
    if (closed) return;
    closed = true;
    if (current?.anchor === anchor) current = null;
    anchor.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', escape, true);
    window.removeEventListener('resize', resized);
    const inside = panel.contains(document.activeElement);
    view.dispose();
    panel.remove();
    if (restore && (inside || document.activeElement === document.body)) anchor.focus();
    options.onClose?.();
  };
  const outside = (event: Event) => {
    const target = event.target as Node;
    if (!panel.contains(target) && !anchor.contains(target)) close(false);
  };
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    // Esc on an item lifted for a keyboard drag (dragList) puts the item back; the panel stays.
    if ((event.target as Element | null)?.closest?.('.drag-lifted')) return;
    event.preventDefault();
    event.stopPropagation();
    close(true);
  };
  // Phones resize the window when the on-screen keyboard opens: keep the panel while the user
  // types in it, and close it only when its button left the screen.
  const resized = () => {
    const box = anchor.getBoundingClientRect();
    const gone = box.bottom < 0 || box.top > window.innerHeight || box.right < 0 || box.left > window.innerWidth;
    if (gone && !panel.contains(document.activeElement)) close(false);
    else place(panel, anchor, options.align ?? 'auto');
  };
  const view = layer(() => html`<div class=${`pop show ${options.className ?? ''}`} role=${options.role ?? 'menu'} aria-label=${options.label ?? null}>${render(() => close(true))}</div>`);
  const panel = view.node as HTMLElement;
  document.body.append(panel);
  place(panel, anchor, options.align ?? 'auto');
  anchor.setAttribute('aria-expanded', 'true');
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('keydown', escape, true);
  window.addEventListener('resize', resized);
  current = { anchor, close };
  return () => close(true);
}

function place(panel: HTMLElement, anchor: Element, align: 'auto' | 'left' | 'side') {
  const box = anchor.getBoundingClientRect(), width = panel.offsetWidth, height = panel.offsetHeight;
  panel.classList.toggle('pop-side', false);
  if (align === 'side' && box.right + 8 + width <= window.innerWidth - 12) {
    panel.classList.add('pop-side');
    panel.style.left = `${box.right + 8}px`;
    panel.style.top = `${Math.max(12, Math.min(box.top, window.innerHeight - height - 12))}px`;
    return;
  }
  let left = align === 'left' || box.left + width < window.innerWidth - 12 ? box.left : box.right - width;
  left = Math.max(12, Math.min(left, window.innerWidth - width - 12));
  let top = box.bottom + 6;
  if (top + height > window.innerHeight - 12) top = Math.max(12, box.top - height - 6);
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

/** Close the open popover, if any. */
export function closePopover() { current?.close(false); }

export type MenuItem =
  | { label: string; icon?: string; tint?: Tint; hint?: string; onSelect: () => void; disabled?: boolean; danger?: boolean }
  | { heading: string }
  | 'separator';

/** A list of menu items. Arrow keys, Home and End move; Enter or Space runs an item. */
export function Menu(items: MenuItem[], close: () => void = () => {}): HTMLElement {
  const nodes = items.map(item => {
    if (item === 'separator') return html`<hr role="separator">`;
    if ('heading' in item) return html`<div class="mh" role="presentation">${item.heading}</div>`;
    return html`<button type="button" class="mi" class:danger-soft=${Boolean(item.danger)} role="menuitem" tabindex="-1" disabled=${Boolean(item.disabled)}
      on:click=${() => { close(); item.onSelect(); }}>${item.icon ? Icon(item.icon, { size: 'sm', tint: item.tint }) : null}<span>${item.label}</span>${item.hint ? html`<kbd class="hint">${item.hint}</kbd>` : null}</button>`;
  });
  const list = html`<div class="menu" role="none">${nodes}</div>` as HTMLElement;
  list.addEventListener('keydown', event => {
    const buttons = [...list.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])')];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined || !buttons.length) return;
    event.preventDefault();
    buttons[(next + buttons.length) % buttons.length].focus();
  });
  queueMicrotask(() => list.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus());
  return list;
}

/** A button that opens a menu of related actions. */
export function MenuButton(props: ButtonProps & { items: MenuItem[] | (() => MenuItem[]); menuLabel?: string }): HTMLButtonElement {
  const open = () => openPopover(button, close => Menu(typeof props.items === 'function' ? props.items() : props.items, close), { label: props.menuLabel ?? (typeof props.label === 'string' ? props.label : undefined) });
  const button = Button({ ...props, onClick: event => { props.onClick?.(event); open(); } });
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  if (props.label !== undefined && !button.querySelector('.chev')) button.append(Icon('chevron-down', { size: 'xs', className: 'chev' }));
  button.addEventListener('keydown', event => { if (event.key === 'ArrowDown' && button.getAttribute('aria-expanded') !== 'true') { event.preventDefault(); open(); } });
  return button;
}

// ---------- dialogs ----------

export interface ModalOptions {
  title: Prop<string>;
  icon?: string;
  tint?: Tint;
  body: (close: () => void) => Child;
  footer?: (close: () => void) => Child;
  /** Cmd or Ctrl Enter, and Mod+S, run this (save the form). Without it Mod+S presses the footer's main action. */
  onSubmit?: () => void;
  onClose?: () => void;
  className?: string;
  /** One column body (action dialogs). */
  narrow?: boolean;
  /** Asked before Esc, the scrim or the close button closes it; false keeps it open. */
  confirmClose?: () => boolean;
}

const stack: Array<() => void> = [];
/** Whether a modal or drawer is open. */
export const layersOpen = cell(0, { name: 'ui.layers' });

function dialog(kind: 'modal' | 'drawer', options: ModalOptions): () => void {
  hideTip();
  closePopover();
  const opener = document.activeElement as HTMLElement | null;
  const titleId = uid('dlg');
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    stack.splice(stack.indexOf(close), 1);
    layersOpen.set(stack.length);
    view.dispose();
    for (const node of nodes) node.parentNode?.removeChild(node);
    options.onClose?.();
    if (opener?.isConnected) opener.focus();
  };
  // Closing by Esc, the scrim or the close button asks first when the layer has something to keep.
  const dismiss = () => { if (options.confirmClose?.() !== false) close(); };
  const keys = (event: KeyboardEvent) => {
    if (event.key === 'Escape' || keyMatches(event, 'Mod+Shift+D')) { event.preventDefault(); event.stopPropagation(); if (!event.repeat) dismiss(); return; }
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && options.onSubmit) { event.preventDefault(); options.onSubmit(); return; }
    // Mod+S is the main action, never the browser's Save Page. A held key does not press it again.
    if (keyMatches(event, 'Mod+S')) {
      event.preventDefault(); event.stopPropagation();
      if (event.repeat) return;
      commitTyped();
      if (options.onSubmit) options.onSubmit(); else primaryAction(panel)?.click();
      return;
    }
    if (moveBetweenActions(panel, event)) return;
    trapFocus(panel, event);
  };
  const head = (closeLabel: string) => html`<div class=${kind === 'modal' ? 'modal-h' : 'drawer-h'}>
    ${options.icon ? html`<span class=${`ph-icon b-${tint(options.tint)}`} style="width:28px;height:28px;border-radius:8px">${Icon(options.icon, { size: 'sm' })}</span>` : null}
    ${kind === 'modal' ? html`<h2 id=${titleId}>${options.title}</h2>` : html`<span class="grow" id=${titleId}>${options.title}</span>`}
    <button type="button" class="icon-btn" aria-label=${closeLabel} on:click=${dismiss}>${Icon('x')}</button></div>`;
  const view = layer(() => kind === 'modal'
    ? html`<div class="scrim show" on:click=${dismiss}></div><div class="modal-wrap show">
        <div class=${`modal ${options.className ?? ''}`} role="dialog" aria-modal="true" aria-labelledby=${titleId} tabindex="-1" on:keydown=${keys}>
          ${head('Close')}<div class="modal-b" style:grid-template-columns=${options.narrow ? '1fr' : null}>${options.body(close)}</div>
          ${options.footer ? html`<div class="modal-f">${options.footer(close)}</div>` : null}</div></div>`
    : html`<div class="scrim show" on:click=${dismiss}></div>
        <aside class=${`drawer show ${options.className ?? ''}`} role="dialog" aria-modal="true" aria-labelledby=${titleId} tabindex="-1" on:keydown=${keys}>
          ${head('Close panel')}<div class="drawer-b">${options.body(close)}</div>
          ${options.footer ? html`<div class="drawer-f">${options.footer(close)}</div>` : null}</aside>`);
  const nodes = view.node instanceof DocumentFragment ? [...view.node.childNodes] : [view.node];
  document.body.append(view.node);
  const panel = nodes.find(node => node instanceof HTMLElement && node.getAttribute('role') === 'dialog') as HTMLElement
    ?? (nodes.find(node => node instanceof HTMLElement && node.classList.contains('modal-wrap')) as HTMLElement).querySelector<HTMLElement>('[role="dialog"]')!;
  stack.push(close);
  layersOpen.set(stack.length);
  // Focus the first field; a dialog without one (a confirm) focuses its main action, so Enter answers it; else the
  // first control, else the dialog. On a touch device the dialog itself, so the on-screen keyboard does not open
  // before the person picks a field.
  const body = panel.querySelector('.modal-b,.drawer-b')!;
  const first = !keyboardFirst() ? panel
    : body.querySelector<HTMLElement>('input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[contenteditable="true"]')
      ?? primaryAction(panel) ?? focusables(body)[0] ?? panel;
  // The main action shows its focus ring even when a shortcut (a key with ⌘ or Ctrl) or a click opened the dialog.
  first.focus(first === primaryAction(panel) ? { focusVisible: true } as FocusOptions : undefined);
  return close;
}

/** The footer's enabled buttons, in order. */
const footerActions = (panel: HTMLElement) =>
  [...panel.querySelectorAll<HTMLButtonElement>(':scope > .modal-f button:not([disabled]), :scope > .drawer-f button:not([disabled])')].filter(button => !button.closest('[hidden]'));

/** A dialog's main action: DialogFooter's submit, else the footer's last primary, danger or success button. */
export function primaryAction(panel: HTMLElement): HTMLButtonElement | null {
  const actions = footerActions(panel);
  return actions.find(button => button.dataset.primary !== undefined)
    ?? actions.filter(button => button.matches('.primary, .danger, .success')).pop() ?? null;
}

// The arrow keys move between the footer's actions, wrapping, while one of them has the focus; Enter presses it.
function moveBetweenActions(panel: HTMLElement, event: KeyboardEvent): boolean {
  const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
  if (!step || event.altKey || event.metaKey || event.ctrlKey || event.shiftKey) return false;
  const actions = footerActions(panel), at = actions.indexOf(document.activeElement as HTMLButtonElement);
  if (at < 0) return false;
  event.preventDefault();
  actions[(at + step + actions.length) % actions.length].focus();
  return true;
}

/** Centred dialog with a header, body and footer. Esc and the scrim close it. Returns close. */
export function openModal(options: ModalOptions): () => void { return dialog('modal', options); }

/** Panel that slides in from the right over the view. Returns close. */
export function openDrawer(options: ModalOptions): () => void { return dialog('drawer', options); }

/** Close every open dialog, newest first. */
export function closeDialogs() { for (const close of [...stack].reverse()) close(); }

// ---------- toast ----------

let toasts: HTMLElement | null = null;
/**
 * Create the toast region. Call it when the app mounts: a live region that exists before its
 * first message is announced reliably. toast() creates it too if it is missing.
 */
export function mountToasts(): HTMLElement {
  if (toasts?.isConnected) return toasts;
  toasts = html`<div class="toasts" role="region" aria-label="Notifications" aria-live="polite"></div>` as HTMLElement;
  document.body.append(toasts);
  return toasts;
}

export interface ToastOptions { icon?: string; undo?: () => void; duration?: number }

/**
 * A short dark confirmation at the bottom of the screen: 2.8 s, or 5 s with Undo. Messages say
 * what happened in the past tense ("Moved to Discovery"). Returns dismiss.
 */
export function toast(text: string, options: ToastOptions = {}): () => void {
  let done = false, timer: ReturnType<typeof setTimeout>;
  const dismiss = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    element.classList.add('out');
    setTimeout(() => { view.dispose(); element.remove(); }, 180);
  };
  const view = layer(() => html`<div class="toast">${Icon(options.icon ?? 'check', { size: 'sm', className: 'ok' })}<span style="flex:1">${text}</span>${options.undo
    ? html`<button type="button" on:click=${() => { options.undo!(); dismiss(); }}>Undo</button>` : null}</div>`);
  const element = view.node as HTMLElement;
  mountToasts().append(element);
  timer = setTimeout(dismiss, options.duration ?? (options.undo ? 5000 : 2800));
  return dismiss;
}

/** Copy text and confirm with a toast showing what was copied. */
export async function copyText(text: string, what = text) {
  try { await navigator.clipboard?.writeText(text); toast(`Copied ${what}`, { icon: 'copy' }); }
  catch { toast(`Could not copy ${what}`, { icon: 'info' }); }
}

/** A dialog's standard footer: a note on the left, Cancel and the main action on the right. The main action
 * is what a dialog without a field focuses and what Mod+S presses. */
export function DialogFooter(props: { note?: Prop<string>; cancel?: () => void; submit: ButtonProps }): Node {
  const submit = Button({ variant: 'primary', ...props.submit });
  submit.dataset.primary = '';
  return html`<span class="grow">${props.note === undefined ? null : () => read(props.note!)}</span>${props.cancel ? Button({ label: 'Cancel', variant: 'ghost', onClick: props.cancel }) : null}${submit}`;
}
