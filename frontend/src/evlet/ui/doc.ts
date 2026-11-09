// Doc view building blocks: DocFrame, DocHeader, MoveButton, StatusBadge, StatusFlow, Tabs and TabPanel,
// SideBlock and LockBanner. Screens compose them with SummaryStrip, FormSection, KeyValueList
// and ChildTable into the one layout every record uses.

import { html, when, type Child } from '../dom';
import { effect, onCleanup, untrack, type Cell, type Readable } from '../reactive';
import type { StateSchema } from '../model/schema';
import { Button, Icon, IconTile, StateBanner, type ButtonProps } from './base';
import { read, tint, uid, type Prop, type Tint } from './core';
import { type FieldLike } from './fields';
import { InlineValue } from './inline';
import { humanize } from './kinds';
import { Menu, openPopover, toast } from './layers';

/** Below this width of the frame the side column leaves it (a drawer, or stacked under the main column). */
export const DOC_NARROW = 960;
/** Main column widths that step the layout down: fewer field columns, then the phone layout, then one column. */
export const DOC_MAIN_STEPS = { m: 640, s: 520, xs: 360 } as const;

/** The width classes a frame of `frame` px with a main column of `main` px gets (0: not measured yet). */
export function docWidthClasses(frame: number, main: number): string[] {
  return [frame > 0 && frame < DOC_NARROW ? 'doc-narrow' : '',
    ...(main > 0 ? (Object.entries(DOC_MAIN_STEPS) as Array<[string, number]>).filter(([, width]) => main < width).map(([step]) => `doc-${step}`) : [])].filter(Boolean);
}

/**
 * The record layout: main column and side column (right, left or off), comfortable or compact.
 * It is responsive to its own width, not the window's: a frame narrower than DOC_NARROW gets `doc-narrow`
 * (the side column leaves the grid), and its main column's width steps `doc-m`, `doc-s` and `doc-xs`; `doc-sized`
 * follows the first measure. A record
 * page and a list's side panel use the same frame; the panel variant only changes its padding and title size.
 */
export function DocFrame(props: { tint: Tint; main: Child; side?: Child; sidePosition?: Prop<'left' | 'right' | 'off'>; density?: Prop<'comfortable' | 'compact'>; variant?: 'page' | 'panel' }): HTMLElement {
  const position = () => read(props.sidePosition ?? 'right');
  const accent = tint(props.tint);
  const frame = html`<div class="doc" class:doc-panel=${props.variant === 'panel'} class:no-side=${() => position() === 'off' || !props.side} class:side-left=${() => position() === 'left'}
    class:compact=${() => read(props.density ?? 'comfortable') === 'compact'} style:--acc=${`var(--t-${accent})`} style:--acc-bg=${`var(--t-${accent}-bg)`}>
    <div class="doc-main">${props.main}</div>${props.side ? when(() => position() !== 'off', () => html`<aside class="doc-side" aria-label="Side panel">${props.side}</aside>`) : null}</div>` as HTMLElement;
  // Measure the frame and its main column; without ResizeObserver (old browsers) it keeps the wide layout.
  if (typeof ResizeObserver !== 'undefined') {
    const main = frame.firstElementChild as HTMLElement;
    const widths = { frame: 0, main: 0 };
    let applied: string[] = [];
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) widths[entry.target === frame ? 'frame' : 'main'] = Math.round(entry.contentRect.width);
      const next = docWidthClasses(widths.frame, widths.main);
      // After the first width, changes may animate (a drawer sliding out, say), not the first layout itself.
      if (widths.frame > 0 && !frame.classList.contains('doc-sized')) requestAnimationFrame(() => frame.classList.add('doc-sized'));
      if (next.join() === applied.join()) return;
      frame.classList.remove(...applied);
      frame.classList.add(...next);
      applied = next;
    });
    observer.observe(frame);
    observer.observe(main);
    onCleanup(() => observer.disconnect());
  }
  return frame;
}

export interface DocHeaderProps {
  /** Record type ("Invoice"): the accessible name of the default mark. The breadcrumb above names it on screen. */
  type: string;
  /** The record type's icon, on the default mark: a tinted square tile, as on the list page. */
  icon: string;
  /** The mark at the start of the title row; defaults to the type's icon tile. */
  mark?: Node;
  /** Tint of the default icon tile. */
  tint?: Tint;
  /** Plain title, or a field edited in place (never a sequence number). */
  title: Prop<string> | FieldLike;
  /** Shown in mono (document numbers). */
  mono?: Prop<boolean>;
  /** Status pills, on the title's line after the title. */
  status?: Child[];
  /** Key facts: money, linked record, dates, owner. One small line under the title row. */
  meta?: Child[];
  /** Outline buttons, then the main move (a SplitButton), then More: right of the title, centred on its row. */
  actions?: Child[];
  /** One muted line under the title ("Desert Rose Retail · 08-11-2026"); nothing shows while it is empty. */
  subtitle?: Prop<string>;
  /** A record image (a round avatar or a square thumbnail) left of the title, in place of the mark. Takes no space
   * while it renders nothing. */
  image?: Child;
  /** Small buttons at the end of the title row, after the actions (a side panel's Expand and Close). */
  tools?: Child[];
}

/**
 * The title row: mark, title and status pills, the main actions on the right; key facts on a small line
 * under it. A narrow header (a side panel, a phone) puts the actions on their own line under the title.
 */
export function DocHeader(props: DocHeaderProps): HTMLElement {
  const field = typeof props.title === 'object' && props.title !== null && 'meta' in props.title ? props.title as FieldLike : null;
  const titleId = uid('doc');
  const title = field && field.meta.type !== 'sequence'
    ? html`<h1 id=${titleId} class="doc-title">${InlineValue({ field, labelledBy: titleId })}</h1>`
    : html`<h1 id=${titleId} class:mono=${props.mono ?? false}>${field ? () => String(field.value.get() ?? '') : props.title as Prop<string>}</h1>`;
  let mark = props.mark;
  if (!mark) {
    const tile = IconTile(props.icon, { tint: props.tint, size: 36 });
    tile.classList.add('doc-tile');
    tile.setAttribute('role', 'img');
    tile.setAttribute('aria-label', props.type);
    mark = tile;
  }
  const status = props.status?.filter(Boolean) ?? [];
  const subtitle = props.subtitle;
  return html`<header class="doc-head"><div class="doc-row">
    <div class="doc-mark">${props.image === undefined ? null : html`<span class="doc-img">${props.image}</span>`}${mark}</div>
    <div class="ttl"><div class="doc-title-line">${title}${status.length ? html`<span class="doc-status">${status.map(item => html`${item}`)}</span>` : null}</div>${
      subtitle === undefined ? null : html`<p class="doc-sub" hidden=${() => !read(subtitle)} title=${() => read(subtitle) || null}>${subtitle}</p>`}</div>
    ${props.actions?.length ? html`<div class="ph-actions">${props.actions}</div>` : null}
    ${props.tools?.length ? html`<div class="doc-tools">${props.tools}</div>` : null}
    ${props.meta?.length ? html`<div class="rec-meta">${props.meta.map(item => html`<span>${item}</span>`)}</div>` : null}</div></header>` as HTMLElement;
}

/**
 * Runs a state move. When its guard fails it stays visible, dims, and explains why on hover,
 * focus or click (tooltip and toast) instead of running.
 */
export function MoveButton(props: ButtonProps & { blocked: Prop<string | null>; onMove: () => void }): HTMLButtonElement {
  const reason = () => read(props.blocked) || null;
  const button = Button({ ...props, tip: reason, onClick: () => { const why = reason(); if (why) toast(why, { icon: 'info' }); else props.onMove(); } });
  const sync = () => { const why = reason(); button.toggleAttribute('data-blocked', Boolean(why)); if (why) button.setAttribute('aria-disabled', 'true'); else button.removeAttribute('aria-disabled'); };
  // Keep the blocked look in step with the guard.
  effect(sync);
  return button;
}

export interface FlowStep { value: string; label: string }

/**
 * The states as connected steps: done steps tint, the current one fills, terminal states turn green or red.
 * Only a step this person can move to now is a button; the others (states only the system reaches, or
 * moves for other roles) are plain steps with no pointer or hover.
 */
export function StatusFlow(props: {
  steps: FlowStep[];
  current: Prop<string | null>;
  /** Terminal states: good ones turn green, bad ones rose. */
  good?: string[];
  bad?: string[];
  /** Whether this person may move to this state now. Without it, every step but the current one may be chosen. */
  canMove?: (to: string) => boolean;
  /** Why a move to this state cannot run right now (unsaved changes, say), or null. */
  blocked?: (to: string) => string | null;
  onMove?: (to: string) => void;
  label?: string;
}): HTMLElement {
  const index = () => props.steps.findIndex(step => step.value === read(props.current));
  return html`<div class="stepper" role="list" aria-label=${props.label ?? 'Status'}>${props.steps.map((step, at) => {
    const done = () => at < index(), now = () => at === index();
    const name = () => `${step.label}${now() ? ', current' : done() ? ', done' : ''}`;
    const open = () => Boolean(props.onMove) && !now() && (props.canMove ? props.canMove(step.value) : true);
    const content = [() => done() ? Icon('check', { size: 'xs' }) : null, step.label];
    return html`<div role="listitem" class="step" class:done=${done} class:cur=${now} class:won=${(props.good ?? []).includes(step.value)}
      class:lost=${(props.bad ?? []).includes(step.value)} class:plain=${() => !open()} aria-current=${() => now() ? 'step' : null}>${() => open()
        ? html`<button type="button" class="step-go" aria-label=${() => `${name()}. Move to ${step.label}`} on:click=${() => {
            const why = props.blocked?.(step.value) ?? null;
            if (why) toast(why, { icon: 'info' }); else props.onMove!(step.value);
          }}>${content}</button>`
        : html`<span class="step-tx" aria-label=${name}>${content}</span>`}</div>`;
  })}</div>` as HTMLElement;
}

export interface BadgeMove { to: string; label: string }

/** How a status badge is drawn: the coloured pill; red text with a ban icon (cancelled); a lock and grey text
 * (locked or final); a dashed pill with a pencil (draft). */
export type BadgeLook = 'pill' | 'cancelled' | 'locked' | 'draft';
const LOOK_ICON: Record<Exclude<BadgeLook, 'pill'>, string> = { cancelled: 'ban', locked: 'lock', draft: 'pencil' };

/**
 * A record's status as a coloured badge, named "Status: Active" for screen readers. With moves this
 * person may take, it opens a menu of them; without, it is plain text with no pointer or hover.
 */
export function StatusBadge(props: {
  value: Prop<string | null>;
  label?: (value: string) => string;
  tint: (value: string) => Tint;
  /** What the badge is, for its accessible name: "Status", "Payment status". */
  name?: string;
  moves?: () => BadgeMove[];
  /** Why a move cannot run right now (unsaved changes, say), or null. */
  blocked?: (to: string) => string | null;
  onMove?: (to: string) => void;
  /** How the value is drawn (BadgeLook); the pill when absent. */
  look?: (value: string) => BadgeLook;
}): Child {
  const label = (value: string) => props.label ? props.label(value) : humanize(value);
  const name = props.name ?? 'Status';
  return () => {
    const value = read(props.value);
    if (value === null || value === undefined || value === '') return null;
    const text = label(String(value)), color = tint(props.tint(String(value)));
    const moves = props.onMove ? props.moves?.() ?? [] : [];
    const look = props.look?.(String(value)) ?? 'pill';
    const inner = look === 'pill' ? [html`<span class="d" aria-hidden="true"></span>`, text] : [Icon(LOOK_ICON[look], { size: 'xs', className: 'ev-status-ic' }), text];
    const kind = look === 'pill' ? `pill ev-status b-${color}` : `ev-status ev-status-${look}${look === 'draft' ? ' pill' : ''}`;
    if (!moves.length) return html`<span class=${kind} role="img" aria-label=${`${name}: ${text}`}>${inner}</span>`;
    const button = html`<button type="button" class=${`${kind} ev-status-go`} aria-haspopup="menu" aria-expanded="false"
      aria-label=${`${name}: ${text}. Change ${name.toLowerCase()}`}>${inner}${Icon('chevron-down', { size: 'xs', className: 'chev' })}</button>` as HTMLButtonElement;
    button.addEventListener('click', () => openPopover(button, close => Menu(moves.map(move => ({ label: move.label, icon: 'arrow-right', onSelect: () => {
      const why = props.blocked?.(move.to) ?? null;
      if (why) toast(why, { icon: 'info' }); else props.onMove!(move.to);
    } })), close), { label: `Change ${name.toLowerCase()}` }));
    return button;
  };
}

/** StatusFlow steps from a model State: its states in order, terminal ones marked. */
export function flowFromState(state: StateSchema): { steps: FlowStep[]; good: string[]; bad: string[] } {
  const bad = (state.final ?? []).filter(name => /void|lost|cancel|reject|unqualified/i.test(name));
  const good = (state.final ?? []).filter(name => !bad.includes(name));
  return { steps: state.states.map(value => ({ value, label: humanize(value) })), good, bad };
}

export interface TabItem { id: string; label: Prop<string>; count?: Prop<number | null> }

/** Row of tabs with an underline in the record accent. Arrow keys move between tabs. */
export function Tabs(props: { tabs: TabItem[]; active: Cell<string>; label: string; idPrefix?: string; doc?: boolean; /** Stick to the top of the scrolling area while the content scrolls under it. */ sticky?: boolean }): HTMLElement {
  const prefix = props.idPrefix ?? uid('tab');
  const buttons = props.tabs.map(tab => html`<button type="button" role="tab" id=${`${prefix}-${tab.id}`} aria-controls=${`${prefix}-${tab.id}-panel`}
    class:on=${() => props.active.get() === tab.id} aria-selected=${() => String(props.active.get() === tab.id)} tabindex=${() => props.active.get() === tab.id ? '0' : '-1'}
    on:click=${() => props.active.set(tab.id)}>${tab.label}${tab.count === undefined ? null : when(() => read(tab.count!) !== null && read(tab.count!) !== undefined,
      () => html`<span class="n num">${() => String(read(tab.count!))}</span>`)}</button>` as HTMLButtonElement);
  const list = html`<div class=${`tabs${props.doc ? ' doc-tabs' : ''}${props.sticky ? ' tabs-sticky' : ''}`} role="tablist" aria-label=${props.label}>${buttons}</div>` as HTMLElement;
  list.addEventListener('keydown', event => {
    const at = props.tabs.findIndex(tab => tab.id === props.active.peek());
    const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: props.tabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const index = (next + props.tabs.length) % props.tabs.length;
    props.active.set(props.tabs[index].id);
    buttons[index].focus();
  });
  list.dataset.prefix = prefix;
  // Where the scrolling area was when the tab changes, read before the new panel shows (keepTabPlace).
  if (props.sticky) effect(() => {
    props.active.get();
    untrack(() => { const scroller = list.isConnected ? scrollParent(list) : null; if (scroller) scrollBefore.set(list, scroller.scrollTop); });
  });
  return list;
}

/** The panel of one tab; rendered only while its tab is active. */
export function TabPanel(tabs: HTMLElement, id: string, active: Cell<string>, render: () => Child): Node {
  const prefix = tabs.dataset.prefix!;
  return when(() => active.get() === id, () => html`<div role="tabpanel" id=${`${prefix}-${id}-panel`} aria-labelledby=${`${prefix}-${id}`} tabindex="0">${render()}</div>`);
}

// A sticky tab bar's scroll position just before its tab changed. Tabs reads it before the new panel shows:
// reading scrollTop after the switch would give the position the browser already pulled up.
const scrollBefore = new WeakMap<HTMLElement, number>();

/** The nearest ancestor of `node` that scrolls vertically, else the document's scrolling element. */
function scrollParent(node: Element): Element | null {
  for (let at = node.parentElement; at; at = at.parentElement) {
    if (/(auto|scroll|overlay)/.test(getComputedStyle(at).overflowY)) return at;
  }
  return document.scrollingElement;
}

/**
 * Keeps the page still when the tab changes, for Tabs made with `sticky`. Call it after the TabPanels, so it
 * runs once the new panel shows.
 * With the tab bar stuck (Tabs sticky), the new panel starts right under it; above that, the scroll position
 * stays. The new panel is given a minimum height (--tab-fill) that keeps that position, so a shorter tab never
 * pulls the page up: it reaches the bottom of the scrolling area and no further.
 */
export function keepTabPlace(tabs: HTMLElement, active: Readable<string>): void {
  let first = true;
  effect(() => {
    const id = active.get();
    if (first) { first = false; return; }
    untrack(() => placeTabPanel(tabs, id));
  });
}

function placeTabPanel(tabs: HTMLElement, id: string) {
  const panel = tabs.parentElement?.querySelector<HTMLElement>(`#${CSS.escape(`${tabs.dataset.prefix}-${id}-panel`)}`);
  const scroller = tabs.isConnected && panel ? scrollParent(tabs) : null;
  if (!panel || !scroller) return;
  const before = scrollBefore.get(tabs) ?? scroller.scrollTop;
  panel.style.removeProperty('--tab-fill');
  const style = getComputedStyle(tabs), view = scroller.getBoundingClientRect(), box = panel.getBoundingClientRect();
  const viewTop = scroller === document.scrollingElement ? 0 : view.top;
  // Positions in the scrolling content, the same whatever the scroll position is now.
  const panelTop = box.top - viewTop + scroller.scrollTop;
  const sticky = style.position === 'sticky';
  // Where the tab bar sticks: the panel's top, less the gap, the bar and its top offset.
  const stuckAt = panelTop - (parseFloat(style.marginBottom) || 0) - tabs.offsetHeight - (parseFloat(style.top) || 0);
  const target = sticky && before >= stuckAt - 1 ? Math.max(0, stuckAt) : before;
  // What follows the panel in its column: its margin, the panels' padding, the save bar.
  const parent = panel.parentElement!, after = (parseFloat(getComputedStyle(parent).paddingBottom) || 0) + (parseFloat(getComputedStyle(panel).marginBottom) || 0)
    + [...parent.children].slice([...parent.children].indexOf(panel) + 1).reduce((sum, next) => {
      const css = getComputedStyle(next);
      return css.position === 'absolute' || css.position === 'fixed' ? sum : sum + (next as HTMLElement).offsetHeight + (parseFloat(css.marginTop) || 0) + (parseFloat(css.marginBottom) || 0);
    }, 0);
  const fill = Math.ceil(target + scroller.clientHeight - panelTop - after);
  if (fill > box.height) panel.style.setProperty('--tab-fill', `${fill}px`);
  if (scroller.scrollTop !== target) scroller.scrollTop = target;
}

/** Titled block inside the side panel, with an optional header action. */
export function SideBlock(props: { title: Prop<string>; action?: Node; body: Child }): HTMLElement {
  const id = uid('side');
  return html`<section class="side-sec" aria-labelledby=${id}><h4><span id=${id}>${props.title}</span>${props.action ?? null}</h4>${props.body}</section>` as HTMLElement;
}

export type Lifecycle = 'draft' | 'locked' | 'void';

/**
 * The record's lifecycle under the header: a draft (what issuing does), locked (with Revise and
 * Void), or void (with the reason).
 */
export function LockBanner(props: {
  lifecycle: Prop<Lifecycle>;
  /** The state's name for the locked banner: "Issued". */
  state?: Prop<string>;
  draftText?: Prop<string>;
  lockedText?: Prop<string>;
  reason?: Prop<string | null>;
  onRevise?: () => void;
  onVoid?: () => void;
}): HTMLElement {
  const lifecycle = () => read(props.lifecycle);
  const actions = [
    props.onRevise ? Button({ label: 'Revise', icon: 'copy', size: 'small', onClick: props.onRevise }) : null,
    props.onVoid ? Button({ label: 'Void', icon: 'circle-x', size: 'small', variant: 'soft-danger', onClick: props.onVoid }) : null,
  ].filter(Boolean) as Node[];
  const shownActions = actions.map(action => when(() => lifecycle() === 'locked', () => action));
  return StateBanner({
    tone: () => lifecycle() === 'void' ? 'danger' : lifecycle() === 'locked' ? 'info' : 'neutral',
    title: () => lifecycle() === 'void' ? 'Void.' : lifecycle() === 'locked' ? `${read(props.state ?? 'Issued')} and locked.` : 'Draft.',
    text: () => lifecycle() === 'void' ? (read(props.reason ?? '') || 'This record was voided.')
      : lifecycle() === 'locked' ? read(props.lockedText ?? 'Only some fields can change. To correct it, void or revise.')
      : read(props.draftText ?? 'Everything can change until it is issued.'),
    actions: shownActions as unknown as Node[],
  });
}
