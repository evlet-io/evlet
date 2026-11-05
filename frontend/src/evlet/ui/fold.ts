// Collapsible form sections. FoldSection is a titled section whose heading is a real button
// (aria-expanded, aria-controls, a chevron) that opens and closes it. Closed, it shows a
// summary ("3 of 5 filled" or "Required missing") and an error count, and its fields leave
// the tab order. A field error inside opens it. Folds keeps each section's state for one
// form: the person's saved choice (per user, per model, by section title) wins over the app's
// starting state. revealField() opens the section that holds a field and focuses the field.

import { html, type Child } from '../dom';
import { cell, derive, effect, untrack, type Cell, type Readable } from '../reactive';
import { Icon } from './base';
import { uid } from './core';
import type { FieldLike } from './fields';

/** How a section starts: open, closed, or closed when every field in it is empty (open on a new record). */
export type FoldStart = boolean | 'empty';

/** Phones (and the narrow side panel's width) start collapsible sections after the first closed. */
export const PHONE = '(max-width: 760px)';
const REVEAL = 'ev-reveal';

const media = (query: string) => { try { return Boolean(window.matchMedia?.(query)?.matches); } catch { return false; } };
/** Whether the height may animate: not when the person asked for reduced motion. */
const motion = () => !media('(prefers-reduced-motion: reduce)');

/** Empty as a summary counts it: no value, blank text, no items. */
export function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === 'string' && value.trim() === '') || (Array.isArray(value) && value.length === 0);
}

/** "3 of 5 filled", or "Required missing" when a required field that applies is empty. */
export function foldSummary(fields: FieldLike[]): string {
  const shown = fields.filter(field => field.rules.get().applies);
  if (shown.some(field => field.rules.get().required && !field.calculated && isEmpty(field.value.get()))) return 'Required missing';
  return `${shown.filter(field => !isEmpty(field.value.get())).length} of ${shown.length} filled`;
}

/** Open and closed state of one form's sections. */
export class Folds {
  private readonly local = new Map<string, Cell<boolean | null>>();

  /**
   * @param saved the person's choices by section title (true is open), kept in their preferences.
   * @param isNew a new record: sections that start closed when empty start open.
   * @param phone phone width: collapsible sections after the first start closed.
   */
  constructor(readonly saved: Cell<Record<string, boolean>>, readonly isNew = false, readonly phone = media(PHONE)) {}

  /** Where a section starts before the person chose: the app's value, the phone rule, then 'empty'. */
  start(collapsed: FoldStart, index: number, fields: FieldLike[]): boolean {
    if (collapsed === true) return false;
    if (this.phone && index > 0) return false;
    if (collapsed === 'empty') return this.isNew || !untrack(() => fields.every(field => isEmpty(field.value.get())));
    return true;
  }

  /** Whether a section is open: opened for an error or a reveal, else the saved choice, else its start. */
  open(title: string, start: boolean): Readable<boolean> {
    const local = this.forced(title);
    return derive(() => local.get() ?? this.saved.get()[title] ?? start, { name: `${title} (open)` });
  }

  private forced(title: string) {
    let found = this.local.get(title);
    if (!found) this.local.set(title, found = cell<boolean | null>(null, { name: `${title} (opened)` }));
    return found;
  }

  /** The person opened or closed it: remember that. */
  toggle(title: string, next: boolean) {
    this.forced(title).set(null);
    this.saved.set({ ...this.saved.peek(), [title]: next });
  }

  /** Open it for now (an error or a reveal) without changing what is saved. */
  reveal(title: string) { this.forced(title).set(true); }

  /** Forget the person's choices: every section goes back to how the app starts it. */
  reset() {
    for (const local of this.local.values()) local.set(null);
    this.saved.set({});
  }

  /** Whether the person has saved any choice (Reset layout has something to do). */
  get changed() { return Object.keys(this.saved.get()).length > 0; }
}

export interface FoldSectionProps {
  title: string;
  /** The fields inside, for the summary, the error count and opening on an error. */
  fields: FieldLike[];
  body: Child;
  note?: string;
  className?: string;
  /** Without folds (or collapsible false) the section always shows, with a plain heading. */
  folds?: Folds | null;
  collapsible?: boolean;
  collapsed?: FoldStart;
  /** The section's place in the form (phones start the ones after the first closed). */
  index?: number;
}

/** A titled section that opens and closes from its heading. */
export function FoldSection(props: FoldSectionProps): HTMLElement {
  const { folds, title } = props;
  if (!folds || props.collapsible === false) {
    return html`<section class=${['fs', props.className ?? ''].join(' ').trim()} aria-label=${title}><div class="fs-h"><h3>${title}</h3>
      ${props.note ? html`<p>${props.note}</p>` : null}</div>${props.body}</section>` as HTMLElement;
  }
  const open = folds.open(title, folds.start(props.collapsed ?? false, props.index ?? 0, props.fields));
  // What is drawn: closing waits for its animation, so the fields leave the tab order once it ends.
  const shown = cell(untrack(() => open.get()), { name: `${title} (shown)` });
  const errors = derive(() => props.fields.filter(field => field.error.get()).length);
  const id = uid('fs');
  const body = html`<div class="fs-body" id=${id} hidden=${() => !shown.get()}>${props.body}</div>` as HTMLElement;
  const toggle = () => folds.toggle(title, !untrack(() => open.get()));
  const section = html`<section class=${['fs fs-fold', props.className ?? ''].join(' ').trim()} class:closed=${() => !open.get()} aria-label=${title} data-section=${title}
    on:ev-reveal=${() => folds.reveal(title)}>
    <div class="fs-h"><h3><button type="button" class="fs-toggle" aria-expanded=${() => String(open.get())} aria-controls=${id}
      on:click=${toggle}>${Icon('chevron-down', { size: 'xs', className: 'fs-chev' })}<span>${title}</span></button>
      <span class="fs-sum muted" hidden=${() => open.get()}>${() => open.get() ? '' : foldSummary(props.fields)}</span>
      <span class="badge fs-err" hidden=${() => !errors.get()} aria-label=${() => errors.get() ? `${errors.get()} ${errors.get() === 1 ? 'error' : 'errors'}` : null}>${() => errors.get() ? String(errors.get()) : ''}</span></h3>
      ${props.note ? html`<p>${props.note}</p>` : null}</div>${body}</section>` as HTMLElement;

  // A new error inside opens the section.
  let before = untrack(() => errors.get());
  effect(() => {
    const now = errors.get();
    if (now > before && !untrack(() => open.get())) untrack(() => folds.reveal(title));
    before = now;
  });
  let first = true;
  effect(() => {
    const next = open.get();
    if (first) { first = false; return; }
    untrack(() => animate(body, next, shown));
  });
  return section;
}

const running = new WeakMap<HTMLElement, Animation>();
function animate(body: HTMLElement, next: boolean, shown: Cell<boolean>) {
  running.get(body)?.cancel();
  if (!motion() || typeof body.animate !== 'function') { shown.set(next); return; }
  if (next) shown.set(true);
  const height = `${body.scrollHeight}px`;
  body.style.overflow = 'hidden';
  const animation = body.animate(next ? [{ height: '0px', opacity: 0 }, { height, opacity: 1 }] : [{ height, opacity: 1 }, { height: '0px', opacity: 0 }],
    { duration: 160, easing: 'ease-out' });
  running.set(body, animation);
  const done = () => { body.style.overflow = ''; running.delete(body); if (!next) shown.set(false); };
  animation.onfinish = done;
  animation.oncancel = () => { body.style.overflow = ''; };
}

/** Open the section that holds a field and focus the field. Returns whether the field was found. */
export function revealField(root: ParentNode, name: string, options: { focus?: boolean } = {}): boolean {
  const target = root.querySelector<HTMLElement>(`[data-field="${CSS.escape(name)}"]`);
  if (!target) return false;
  target.dispatchEvent(new CustomEvent(REVEAL, { bubbles: true }));
  target.scrollIntoView?.({ block: 'center' });
  if (options.focus !== false) {
    // An editable control first; a read-only or calculated value (out of the Tab order) still takes focus from here.
    const control = target.querySelector<HTMLElement>('input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled), [tabindex]:not([tabindex="-1"])')
      ?? target.querySelector<HTMLElement>('.fval') ?? target.querySelector<HTMLElement>('[tabindex]');
    control?.focus();
  }
  return true;
}

/** After a refused save: open every section with an error, then scroll to and focus the first error. */
export function revealFirstError(root: ParentNode, fields: FieldLike[]): boolean {
  const failed = fields.find(field => untrack(() => field.error.get()) && root.querySelector(`[data-field="${CSS.escape(field.meta.name)}"]`));
  for (const field of fields) if (untrack(() => field.error.get())) root.querySelector(`[data-field="${CSS.escape(field.meta.name)}"]`)?.dispatchEvent(new CustomEvent(REVEAL, { bubbles: true }));
  return failed ? revealField(root, failed.meta.name) : false;
}

/** How a section orders its fields: across then down (rows), or down the left column, then the right (columns). */
export type Flow = 'rows' | 'columns';

/**
 * A section's fields in their order. rows is one two-column grid, filled across then down. columns
 * splits the fields at each full-width one: each run goes down the left column, then the right, and
 * a full-width field sits between runs like a section break. The DOM order is the order on screen, so
 * Tab follows it; on a phone the columns stack in the same order.
 */
export function flowGrid(cells: Array<{ node: Node; wide: boolean }>, flow: Flow = 'rows'): HTMLElement | null {
  if (!cells.length) return null;
  if (flow !== 'columns') return html`<div class="fs-g">${cells.map(cell => cell.node)}</div>` as HTMLElement;
  const parts: Node[] = [];
  let run: Node[] = [];
  const close = () => {
    if (!run.length) return;
    const half = Math.ceil(run.length / 2);
    parts.push(html`<div class="fs-colrun"><div class="fs-col">${run.slice(0, half)}</div><div class="fs-col">${run.slice(half)}</div></div>`);
    run = [];
  };
  for (const cell of cells) {
    if (cell.wide) { close(); parts.push(cell.node); } else run.push(cell.node);
  }
  close();
  return html`<div class="fs-g fs-cols">${parts}</div>` as HTMLElement;
}
