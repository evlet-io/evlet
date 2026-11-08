// The side panel's Related block: one compact row per related model (icon, name, count, chevron),
// most records first; relations without records are left out (the Related tab lists them). Five rows show at first
// and "Show all" expands the rest in place.

import { html, when, type Child } from '../dom';
import type { Cell, Readable } from '../reactive';
import { Icon } from '../ui/base';

export const RELATED_LIMIT = 5;

export interface RelatedItem {
  key: string;
  label: string;
  href: string;
  icon: string;
  tint: string;
  /** Null until the count has loaded. */
  count: Readable<number | null>;
  /** What one and several of its records are called ('invoice', 'invoices'); 'record' and 'records' without it. */
  noun?: { one: string; many: string };
}

/** '1 invoice', '3 invoices'; '1 record', '3 records' for a relation without its own noun. */
export const countText = (count: number, noun = { one: 'record', many: 'records' }) => `${count} ${count === 1 ? noun.one : noun.many}`;

/** With records first (most to fewest, then by label), then the ones still loading; empty ones apart. */
export function arrangeRelated(items: RelatedItem[]) {
  const byLabel = (a: RelatedItem, b: RelatedItem) => a.label.localeCompare(b.label);
  const counted = items.filter(item => (item.count.get() ?? 0) > 0).sort((a, b) => (b.count.get()! - a.count.get()!) || byLabel(a, b));
  const loading = items.filter(item => item.count.get() === null).sort(byLabel);
  const empty = items.filter(item => item.count.get() === 0).sort(byLabel);
  return { rows: [...counted, ...loading], empty };
}

export function RelatedPanel(props: { items: RelatedItem[]; expanded: Cell<boolean>; onOpen: (item: RelatedItem) => void }): Child {
  // Plain left clicks switch tab in place; modified clicks keep the link's own behaviour.
  const open = (item: RelatedItem) => (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    props.onOpen(item);
  };
  const row = (item: RelatedItem) => {
    const count = item.count.get();
    return html`<li><a class="rel-row" href=${item.href} aria-label=${count === null ? item.label : `${item.label}, ${countText(count, item.noun)}`} on:click=${open(item)}>
      <span class="rel-ic b-${item.tint}" aria-hidden="true">${Icon(item.icon, { size: 'xs' })}</span><span class="rel-nm">${item.label}</span>${count === null ? null : html`<span class="rel-ct num">${String(count)}</span>`}${Icon('chevron-right', { size: 'sm', className: 'muted' })}</a></li>`;
  };
  return html`<div class="rel">${() => {
    // Relations without records are left out; the Related tab lists them.
    const { rows } = arrangeRelated(props.items);
    const open_ = props.expanded.get();
    const shown = open_ ? rows : rows.slice(0, RELATED_LIMIT);
    return [
      shown.length ? html`<ul class="rel-list">${shown.map(row)}</ul>` : null,
      rows.length > RELATED_LIMIT ? html`<button type="button" class="rel-all" aria-expanded=${String(open_)} on:click=${() => props.expanded.set(!open_)}>${open_ ? 'Show fewer' : `Show all (${rows.length})`}</button>` : null,
    ];
  }}</div>`;
}
