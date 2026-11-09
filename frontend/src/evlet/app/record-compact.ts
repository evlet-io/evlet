// The parts of a compact record page (docs/ui.md, Compact record page): the key cards, "more information" rows
// that open and close with a one-line summary while closed, and the summary texts. The record page puts them
// together; nothing here knows a model.

import { html, when, type Child } from '../dom';
import { cell, derive, effect, untrack, type Readable } from '../reactive';
import { Icon, StatCard, type StatProps } from '../ui/base';
import { uid } from '../ui/core';
import { isEmpty, type Folds } from '../ui/fold';
import type { FieldLike, LinkSource } from '../ui/fields';
import { formatOf } from './meta';
import { plainOf } from '../richtext/html';

/** The key cards of a compact page (Meta.cards): the list page's number cards (StatCard in a summary strip), above the
 * white card. Each is a field's plain label over its value, with no caption. data-count lets the narrow layout
 * (screens.css) put them two or three to a row with an odd last card across the row. */
export function CardRow(stats: StatProps[]): HTMLElement {
  const shown = stats.slice(0, 5).map(stat => ({ ...stat, note: undefined }));
  return html`<div class="sum ev-cards" data-count=${String(shown.length)} style:--cards=${String(shown.length)}>${shown.map(StatCard)}</div>` as HTMLElement;
}

/** Where a record image's picture is: a URL or path string, or an object with a url (an uploaded file). Anything
 * else (no value, an id the browser can't show) is null, and the header shows no image. */
export function imageSource(value: unknown): string | null {
  const url = typeof value === 'string' ? value : value && typeof value === 'object' && typeof (value as { url?: unknown }).url === 'string' ? (value as { url: string }).url : '';
  return /^(https?:\/\/|\/(?!\/)|data:image\/)/i.test(url.trim()) ? url.trim() : null;
}

/** The record image left of the title: a square thumbnail, or a round avatar for a person. Nothing without a picture. */
export function RecordImage(source: () => string | null, props: { round: boolean; label: () => string }): Child {
  return () => {
    const src = source();
    return src ? html`<img class="doc-img-pic" class:round=${props.round} src=${src} alt=${props.label} width="36" height="36" loading="lazy" />` : null;
  };
}

/** A field's value as plain text for a one-line summary: a Link as its record's title, rich text as text. */
export function valueText(field: FieldLike, links: LinkSource | null): string {
  const meta = field.meta, value = field.value.get();
  if (isEmpty(value)) return '';
  if (meta.type === 'link' && meta.target && links) return links.title(meta.target, String(value)).get() ?? '…';
  if (meta.type === 'richtext') return plainOf(String(value)).replace(/\s+/g, ' ').trim();
  const text = formatOf(meta, value).replace(/\s+/g, ' ').trim();
  const unit = (meta.type === 'integer' || meta.type === 'decimal') && meta.suffix ? (meta.suffix === '%' ? '%' : ` ${meta.suffix}`) : '';
  return text + unit;
}

/** "Label: value · Label: Not set · ..." over the fields that apply, in their order. */
export function fieldsSummary(fields: FieldLike[], links: LinkSource | null): string {
  return fields.filter(field => field.rules.get().applies).map(field => `${field.meta.label}: ${valueText(field, links) || 'Not set'}`).join(' · ');
}

/** "3 rows", "1 row" or "No rows". */
export const rowsText = (count: number) => count ? `${count} ${count === 1 ? 'row' : 'rows'}` : 'No rows';

/** The singular of a table's label for "Add milestone": a trailing s goes; anything else reads "row". */
export function rowNoun(label: string): string {
  return /[^s]s$/i.test(label) ? label.slice(0, -1).toLowerCase() : 'row';
}

export interface MoreRowProps {
  title: string;
  description?: string;
  /** Shown on one line while the row is closed. Empty: "Nothing filled in". */
  summary: () => string;
  /** The content, drawn the first time the row opens (or at once with eager). */
  body: () => Child;
  folds: Folds;
  /** Whether it starts open before the person chose. */
  start: boolean;
  /** Drawn at once even while closed, so a field inside can be revealed and its errors counted. */
  eager?: boolean;
  /** Fields inside: an error opens the row and shows a count while it is closed. */
  fields?: FieldLike[];
  /** Text shown while closed with nothing to summarise. */
  none?: string;
}

/** One "more information" row: title, description and chevron on the left, the content (or its one-line summary
 * while closed) on the right. The person's choice is kept like a section's (Folds, by title). */
export function MoreRow(props: MoreRowProps): HTMLElement {
  const { folds, title } = props;
  const open = folds.open(title, props.start);
  const drawn = cell(props.eager || untrack(() => open.get()));
  effect(() => { if (open.get()) drawn.set(true); });
  const fields = props.fields ?? [];
  const errors = derive(() => fields.filter(field => field.error.get()).length);
  let before = untrack(() => errors.get());
  effect(() => {
    const now = errors.get();
    if (now > before && !untrack(() => open.get())) untrack(() => folds.reveal(title));
    before = now;
  });
  const id = uid('more'), head = uid('more-h');
  const summary: Readable<string> = derive(() => open.get() ? '' : props.summary());
  return html`<section class="ev-more-row" class:closed=${() => !open.get()} aria-labelledby=${head} data-section=${title}
    on:ev-reveal=${() => folds.reveal(title)}>
    <div class="ev-more-l">
      <button type="button" class="ev-more-toggle" id=${head} aria-expanded=${() => String(open.get())} aria-controls=${id}
        on:click=${() => folds.toggle(title, !untrack(() => open.get()))}>${Icon('chevron-down', { size: 'xs', className: 'ev-more-chev' })}<span>${title}</span></button>
      ${props.description ? html`<p class="ev-more-desc">${props.description}</p>` : null}
      <span class="badge fs-err" hidden=${() => !errors.get()}>${() => errors.get() ? `${errors.get()} ${errors.get() === 1 ? 'error' : 'errors'}` : ''}</span>
    </div>
    <div class="ev-more-r">
      <p class="ev-more-sum" hidden=${() => open.get()} title=${() => summary.get() || null}>${() => summary.get() || html`<span class="ev-more-none">${props.none ?? 'Nothing filled in'}</span>`}</p>
      <div class="ev-more-body" id=${id} hidden=${() => !open.get()}>${when(() => drawn.get(), props.body)}</div>
    </div></section>` as HTMLElement;
}
