// Doc view building blocks: DocFrame, DocHeader, MoveButton, StatusFlow, Tabs and TabPanel,
// SideBlock and LockBanner. Screens compose them with SummaryStrip, FormSection, KeyValueList
// and ChildTable into the one layout every record uses.

import { html, when, type Child } from '../dom';
import { effect, type Cell } from '../reactive';
import type { StateSchema } from '../model/schema';
import { Button, Icon, StateBanner, type ButtonProps } from './base';
import { read, tint, uid, type Prop, type Tint } from './core';
import { type FieldLike } from './fields';
import { InlineValue } from './inline';
import { humanize } from './kinds';
import { toast } from './layers';

/** The record layout: main column and side panel (right, left or off), comfortable or compact. */
export function DocFrame(props: { tint: Tint; main: Child; side?: Child; sidePosition?: Prop<'left' | 'right' | 'off'>; density?: Prop<'comfortable' | 'compact'> }): HTMLElement {
  const position = () => read(props.sidePosition ?? 'right');
  const accent = tint(props.tint);
  return html`<div class="doc" class:no-side=${() => position() === 'off' || !props.side} class:side-left=${() => position() === 'left'}
    class:compact=${() => read(props.density ?? 'comfortable') === 'compact'} style:--acc=${`var(--t-${accent})`} style:--acc-bg=${`var(--t-${accent}-bg)`}>
    <div class="doc-main">${props.main}</div>${props.side ? when(() => position() !== 'off', () => html`<aside class="doc-side" aria-label="Side panel">${props.side}</aside>`) : null}</div>` as HTMLElement;
}

export interface DocHeaderProps {
  /** Record type, shown as the eyebrow: "Invoice". */
  type: string;
  icon: string;
  mark: Node;
  /** Plain title, or a field edited in place (never a sequence number). */
  title: Prop<string> | FieldLike;
  /** Shown in mono (document numbers). */
  mono?: Prop<boolean>;
  /** Key facts: money, linked record, dates, owner. */
  meta?: Child[];
  actions?: Child[];
}

/** Record mark, record type, title, key facts and the main actions. */
export function DocHeader(props: DocHeaderProps): HTMLElement {
  const field = typeof props.title === 'object' && props.title !== null && 'meta' in props.title ? props.title as FieldLike : null;
  const titleId = uid('doc');
  const title = field && field.meta.type !== 'sequence'
    ? html`<h1 id=${titleId} class="doc-title">${InlineValue({ field, labelledBy: titleId })}</h1>`
    : html`<h1 id=${titleId} class:mono=${props.mono ?? false}>${field ? () => String(field.value.get() ?? '') : props.title as Prop<string>}</h1>`;
  return html`<header class="doc-head">
    <div class="doc-mark">${props.mark}</div>
    <div class="ttl"><div class="eyebrow">${Icon(props.icon, { size: 'xs' })}${props.type}</div>${title}${props.meta?.length ? html`<div class="rec-meta">${props.meta.map(item => html`<span>${item}</span>`)}</div>` : null}</div>
    ${props.actions?.length ? html`<div class="ph-actions">${props.actions}</div>` : null}</header>` as HTMLElement;
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

/** The states as connected steps: done steps tint, the current one fills, terminal states turn green or red. */
export function StatusFlow(props: {
  steps: FlowStep[];
  current: Prop<string | null>;
  /** Terminal states: good ones turn green, bad ones rose. */
  good?: string[];
  bad?: string[];
  /** Why a move to this state is not allowed now, or null. */
  blocked?: (to: string) => string | null;
  onMove?: (to: string) => void;
  label?: string;
}): HTMLElement {
  const index = () => props.steps.findIndex(step => step.value === read(props.current));
  return html`<div class="stepper" role="list" aria-label=${props.label ?? 'Status'}>${props.steps.map((step, at) => {
    const done = () => at < index(), now = () => at === index();
    return html`<button type="button" role="listitem" class="step" class:done=${done} class:cur=${now} class:won=${(props.good ?? []).includes(step.value)}
      class:lost=${(props.bad ?? []).includes(step.value)} aria-current=${() => now() ? 'step' : null}
      aria-label=${() => `${step.label}${now() ? ', current' : done() ? ', done' : ''}`}
      on:click=${() => {
        if (now() || !props.onMove) return;
        const why = props.blocked?.(step.value) ?? null;
        if (why) toast(why, { icon: 'info' }); else props.onMove(step.value);
      }}>${() => done() ? Icon('check', { size: 'xs' }) : null}${step.label}</button>`;
  })}</div>` as HTMLElement;
}

/** StatusFlow steps from a model State: its states in order, terminal ones marked. */
export function flowFromState(state: StateSchema): { steps: FlowStep[]; good: string[]; bad: string[] } {
  const bad = (state.final ?? []).filter(name => /void|lost|cancel|reject|unqualified/i.test(name));
  const good = (state.final ?? []).filter(name => !bad.includes(name));
  return { steps: state.states.map(value => ({ value, label: humanize(value) })), good, bad };
}

export interface TabItem { id: string; label: string; count?: Prop<number | null> }

/** Row of tabs with an underline in the record accent. Arrow keys move between tabs. */
export function Tabs(props: { tabs: TabItem[]; active: Cell<string>; label: string; idPrefix?: string; doc?: boolean }): HTMLElement {
  const prefix = props.idPrefix ?? uid('tab');
  const buttons = props.tabs.map(tab => html`<button type="button" role="tab" id=${`${prefix}-${tab.id}`} aria-controls=${`${prefix}-${tab.id}-panel`}
    class:on=${() => props.active.get() === tab.id} aria-selected=${() => String(props.active.get() === tab.id)} tabindex=${() => props.active.get() === tab.id ? '0' : '-1'}
    on:click=${() => props.active.set(tab.id)}>${tab.label}${tab.count === undefined ? null : when(() => read(tab.count!) !== null && read(tab.count!) !== undefined,
      () => html`<span class="n num">${() => String(read(tab.count!))}</span>`)}</button>` as HTMLButtonElement);
  const list = html`<div class=${props.doc ? 'tabs doc-tabs' : 'tabs'} role="tablist" aria-label=${props.label}>${buttons}</div>` as HTMLElement;
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
  return list;
}

/** The panel of one tab; rendered only while its tab is active. */
export function TabPanel(tabs: HTMLElement, id: string, active: Cell<string>, render: () => Child): Node {
  const prefix = tabs.dataset.prefix!;
  return when(() => active.get() === id, () => html`<div role="tabpanel" id=${`${prefix}-${id}-panel`} aria-labelledby=${`${prefix}-${id}`} tabindex="0">${render()}</div>`);
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
