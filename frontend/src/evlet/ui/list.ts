// List building blocks: PageHeader, Toolbar, Filters, BulkBar, Board (columns by state with
// counts, totals and drag to move) and BoardCard. Saved views use Tabs from doc.ts.

import { each, html, when, type Child } from '../dom';
import { cell, derive, type Cell } from '../reactive';
import { Avatar, Button, FilterChip, Icon, IconTile, ProgressBar, RecordMark, StatusPill, Chip } from './base';
import { compact, read, tint, type Prop, type Tint } from './core';
import { toast } from './layers';

/** Page icon, title, optional count and the page actions on the right. */
export function PageHeader(props: { icon: string; tint?: Tint; title: Prop<string>; count?: Prop<number | string | null>; actions?: Child[] }): HTMLElement {
  return html`<div class="ph">${IconTile(props.icon, { tint: props.tint, size: 32 })}<h1>${props.title}</h1>${props.count === undefined ? null
    : html`<span class="count num">${() => { const count = read(props.count!); return count === null || count === undefined ? '' : String(count); }}</span>`}<span class="grow"></span>
    ${props.actions?.length ? html`<div class="ph-actions">${props.actions}</div>` : null}</div>` as HTMLElement;
}

/** Search, filters and a summary in one wrapping row above a list. */
export function Toolbar(props: { left?: Child[]; right?: Child[]; label?: string }): HTMLElement {
  return html`<div class="toolbar" role="toolbar" aria-label=${props.label ?? 'List tools'}>${props.left ?? []}<span class="grow"></span>${props.right ?? []}</div>` as HTMLElement;
}

export interface ActiveFilter { key: string; label: string }

/** Active filters as chips, each with a remove button, and Clear when there are any. */
export function Filters(props: { filters: Cell<ActiveFilter[]>; onChange?: (filters: ActiveFilter[]) => void }): HTMLElement {
  const set = (next: ActiveFilter[]) => { props.filters.set(next); props.onChange?.(next); };
  return html`<span class="filters">${each(props.filters, filter => filter.key, filter => FilterChip({ label: () => filter().label,
    onRemove: () => set(props.filters.peek().filter(item => item.key !== filter().key)) }))}${when(() => props.filters.get().length > 0,
      () => Button({ label: 'Clear', variant: 'ghost', size: 'small', onClick: () => set([]) }))}</span>` as HTMLElement;
}

/** A summary on the right of a toolbar: "12 deals · AED 1.15M". */
export function ListSummary(items: Array<{ label: string; value: Prop<string> }>): HTMLElement {
  return html`<span class="summary">${items.map(item => html`<span>${item.label} <b class="num">${item.value}</b></span>`)}</span>` as HTMLElement;
}

export interface BulkAction { label: string; icon: string; onClick: () => void }

/** Dark bar that rises from the bottom when rows are selected. */
export function BulkBar(props: { count: Prop<number>; actions: BulkAction[]; onClear: () => void; noun?: string }): HTMLElement {
  const count = () => Number(read(props.count)) || 0;
  return html`<div class="bulk" class:show=${() => count() > 0} role="toolbar" aria-label="Bulk actions" aria-hidden=${() => count() > 0 ? null : 'true'}
    inert=${() => count() === 0}>
    <span class="n" role="status">${() => `${count()} selected`}</span>
    ${props.actions.map(action => html`<button type="button" on:click=${action.onClick}>${Icon(action.icon, { size: 'sm' })}${action.label}</button>`)}
    <button type="button" aria-label="Clear selection" on:click=${props.onClear}>${Icon('x', { size: 'sm' })}</button></div>` as HTMLElement;
}

// ---------- board ----------

export interface BoardColumn { value: string; label: string; tint: Tint }
/** A column's totals from the server: the record count, a formatted amount and its share (0 to 100). */
export interface BoardTotal { count: number; amount?: Child; share?: number }

export interface BoardProps<T> {
  columns: BoardColumn[];
  cards: Prop<readonly T[]>;
  key: (card: T) => string | number;
  /** The column a card is in (its state). */
  group: (card: T) => string;
  /** The card's value for column totals (a number of money units). */
  measure?: (card: T) => number;
  /**
   * Column totals from the server, by column value. When set, they replace the count and
   * measure of the loaded cards; null (not known yet) falls back to the loaded cards' count.
   */
  totals?: () => Record<string, BoardTotal> | null;
  /** Show an amount and share bar from `totals`. */
  amounts?: boolean;
  card: (card: () => T) => Node;
  /** Move a card; return a reason to refuse it. The board confirms with Undo when the card may move back. */
  onMove: (card: T, to: string) => string | null | void;
  /** Why a card may not move to a column now, or null. Checked before onMove and to decide whether Undo is offered. */
  canMove?: (card: T, to: string) => string | null;
  onAdd?: (column: BoardColumn) => void;
  addLabel?: string;
  currency?: string;
  label: string;
}

/** Columns by state with counts, totals and a share bar. Cards drag between columns. */
export function Board<T>(props: BoardProps<T>): HTMLElement {
  const groups = derive(() => {
    const map = new Map<string, T[]>(props.columns.map(column => [column.value, []]));
    for (const card of read(props.cards) ?? []) map.get(props.group(card))?.push(card);
    return map;
  });
  const sumOf = (cards: readonly T[]) => cards.reduce((sum, card) => sum + (props.measure?.(card) ?? 0), 0);
  const grand = derive(() => sumOf(read(props.cards) ?? []));
  const over = cell<string | null>(null);
  let dragged: T | null = null;
  // The card as it is now (onMove may replace it), found by its key.
  const latest = (card: T) => (read(props.cards) ?? []).find(item => props.key(item) === props.key(card)) ?? card;
  const move = (card: T, to: string) => {
    const from = props.group(card);
    if (from === to) return;
    const reason = props.canMove?.(card, to) || props.onMove(card, to);
    if (reason) { toast(reason, { icon: 'info' }); return; }
    const label = props.columns.find(column => column.value === to)?.label ?? to;
    // Undo is a move back, offered only when the card may make it.
    const now = latest(card);
    const back = !props.canMove?.(now, from);
    toast(`Moved to ${label}`, back ? { undo: () => {
      const card = latest(now), why = props.canMove?.(card, from) || props.onMove(card, from);
      if (why) toast(why, { icon: 'info' });
    } } : {});
  };
  const column = (item: BoardColumn) => {
    const cards = () => groups.get().get(item.value) ?? [];
    const total = () => sumOf(cards());
    const server = () => props.totals?.() ?? null;
    const count = () => server() ? server()![item.value]?.count ?? 0 : cards().length;
    const amount = () => server() ? server()![item.value]?.amount ?? '' : props.measure ? compact(total()) : '';
    const share = () => server() ? Math.round(server()![item.value]?.share ?? 0) : grand.get() ? Math.round(total() / grand.get() * 100) : 0;
    const showAmount = Boolean(props.measure || props.amounts);
    const body = html`<div class="col-b" role="list" aria-label=${item.label}>${each(cards, props.key, card => {
      const element = props.card(card) as HTMLElement;
      element.setAttribute('draggable', 'true');
      element.setAttribute('role', 'listitem');
      element.tabIndex = 0;
      element.addEventListener('dragstart', () => { dragged = card(); element.classList.add('dragging'); });
      element.addEventListener('dragend', () => { dragged = null; element.classList.remove('dragging'); over.set(null); });
      element.addEventListener('keydown', event => {
        if (!event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
        event.preventDefault();
        const at = props.columns.findIndex(column => column.value === props.group(card()));
        const target = props.columns[at + (event.key === 'ArrowLeft' ? -1 : 1)];
        if (target) move(card(), target.value);
      });
      return element;
    }, { fallback: () => html`<div class="col-empty">Nothing here. Drag a card in.</div>` })}</div>` as HTMLElement;
    const section = html`<section class="col-k" class:over=${() => over.get() === item.value} aria-label=${() => `${item.label}, ${count()}`}>
      <div class="col-h"><div class="r1">${StatusPill({ label: item.label, tint: item.tint })}<span class="ct num">${() => String(count())}</span><span class="grow"></span>
        ${showAmount ? html`<span class="amt num">${amount}</span>` : null}</div>
        ${showAmount ? ProgressBar({ value: share, tint: item.tint, showValue: false, label: `${item.label} share of value` }) : null}</div>
      ${body}${props.onAdd ? html`<button type="button" class="col-add" on:click=${() => props.onAdd!(item)}>${Icon('plus', { size: 'sm' })}${props.addLabel ?? 'Add'}</button>` : null}</section>` as HTMLElement;
    section.addEventListener('dragover', event => { if (dragged) { event.preventDefault(); over.set(item.value); } });
    section.addEventListener('dragleave', event => { if (!section.contains(event.relatedTarget as Node)) over.set(null); });
    section.addEventListener('drop', event => { event.preventDefault(); over.set(null); if (dragged) move(dragged, item.value); });
    return section;
  };
  return html`<div class="board-wrap"><div class="board" role="group" aria-label=${props.label}>${props.columns.map(column)}</div></div>` as HTMLElement;
}

/** Draggable record card: tags, title, linked record, value, date and owner. */
export function BoardCard(props: { title: Prop<string>; tags?: Prop<string[]>; company?: Prop<string>; value?: Prop<Child>; date?: Prop<string>; overdue?: Prop<boolean>;
  owner?: Prop<string>; onOpen?: () => void; tint?: Tint; /** A cancelled record: the title struck through, the card greyed. */ cancelled?: Prop<boolean>;
  /** A small icon before the title, such as the record's state (a ban, a lock or a pencil). */ mark?: () => Child }): HTMLElement {
  const card = html`<article class="card" class:is-cancelled=${props.cancelled ?? false} on:click=${() => props.onOpen?.()} on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') props.onOpen?.(); }}>
    ${props.tags ? html`<div class="tags">${() => (read(props.tags!) ?? []).map(tag => Chip({ label: tag }))}</div>` : null}
    <div class="t">${props.mark ?? null}${props.title}</div>
    ${props.company ? html`<div class="o">${RecordMark({ name: props.company })}<span>${props.company}</span></div>` : null}
    <div class="f">${props.value ? html`<span class="v num">${props.value}</span>` : null}<span class="grow"></span>
      ${props.date ? html`<span class="dt" class:due-over=${props.overdue ?? false}>${Icon('calendar', { size: 'xs' })}${props.date}</span>` : null}
      ${props.owner ? Avatar({ name: props.owner, size: 'sm' }) : null}</div></article>` as HTMLElement;
  if (props.tint) card.style.setProperty('--acc', `var(--t-${tint(props.tint)})`);
  return card;
}
