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

export interface BoardProps<T> {
  columns: BoardColumn[];
  cards: Prop<readonly T[]>;
  key: (card: T) => string | number;
  /** The column a card is in (its state). */
  group: (card: T) => string;
  /** The card's value for column totals (a number of money units). */
  measure?: (card: T) => number;
  card: (card: () => T) => Node;
  /** Move a card; return a reason to refuse it. The board confirms with Undo. */
  onMove: (card: T, to: string) => string | null | void;
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
  const move = (card: T, to: string) => {
    const from = props.group(card);
    if (from === to) return;
    const reason = props.onMove(card, to);
    if (reason) { toast(reason, { icon: 'info' }); return; }
    const label = props.columns.find(column => column.value === to)?.label ?? to;
    toast(`Moved to ${label}`, { undo: () => { props.onMove(card, from); } });
  };
  const column = (item: BoardColumn) => {
    const cards = () => groups.get().get(item.value) ?? [];
    const total = () => sumOf(cards());
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
    const section = html`<section class="col-k" class:over=${() => over.get() === item.value} aria-label=${() => `${item.label}, ${cards().length}`}>
      <div class="col-h"><div class="r1">${StatusPill({ label: item.label, tint: item.tint })}<span class="ct num">${() => String(cards().length)}</span><span class="grow"></span>
        ${props.measure ? html`<span class="amt num">${() => compact(total())}</span>` : null}</div>
        ${props.measure ? ProgressBar({ value: () => grand.get() ? Math.round(total() / grand.get() * 100) : 0, tint: item.tint, showValue: false, label: `${item.label} share of value` }) : null}</div>
      ${body}${props.onAdd ? html`<button type="button" class="col-add" on:click=${() => props.onAdd!(item)}>${Icon('plus', { size: 'sm' })}${props.addLabel ?? 'Add'}</button>` : null}</section>` as HTMLElement;
    section.addEventListener('dragover', event => { if (dragged) { event.preventDefault(); over.set(item.value); } });
    section.addEventListener('dragleave', event => { if (!section.contains(event.relatedTarget as Node)) over.set(null); });
    section.addEventListener('drop', event => { event.preventDefault(); over.set(null); if (dragged) move(dragged, item.value); });
    return section;
  };
  return html`<div class="board-wrap"><div class="board" role="group" aria-label=${props.label}>${props.columns.map(column)}</div></div>` as HTMLElement;
}

/** Draggable record card: tags, title, linked record, value, date and owner. */
export function BoardCard(props: { title: Prop<string>; tags?: Prop<string[]>; company?: Prop<string>; value?: Prop<string>; date?: Prop<string>; overdue?: Prop<boolean>;
  owner?: Prop<string>; onOpen?: () => void; tint?: Tint }): HTMLElement {
  const card = html`<article class="card" on:click=${() => props.onOpen?.()} on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') props.onOpen?.(); }}>
    ${props.tags ? html`<div class="tags">${() => (read(props.tags!) ?? []).map(tag => Chip({ label: tag }))}</div>` : null}
    <div class="t">${props.title}</div>
    ${props.company ? html`<div class="o">${RecordMark({ name: props.company })}<span>${props.company}</span></div>` : null}
    <div class="f">${props.value ? html`<span class="v num">${props.value}</span>` : null}<span class="grow"></span>
      ${props.date ? html`<span class="dt" class:due-over=${props.overdue ?? false}>${Icon('calendar', { size: 'xs' })}${props.date}</span>` : null}
      ${props.owner ? Avatar({ name: props.owner, size: 'sm' }) : null}</div></article>` as HTMLElement;
  if (props.tint) card.style.setProperty('--acc', `var(--t-${tint(props.tint)})`);
  return card;
}
