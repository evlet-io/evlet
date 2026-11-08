// Recent records in the top bar: a small history button at the left end, just before the breadcrumb, that opens this session's
// recently opened records, newest first, each with its model and when it was last opened.
// The list lives in memory only (Prefs.recentRecords) and is dropped on sign-out.

import { html } from '../dom';
import { Icon } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { openPopover } from '../ui/layers';
import { exactTime, minuteClock, relativeTime } from '../ui/time';
import type { AppContext } from './context';
import { identity, type ScreenModel } from './meta';
import type { RecentRecord } from './prefs';
import { SHELL_ICONS } from './nav-icons';

export function RecentsButton(ctx: AppContext, models: ScreenModel[]): HTMLElement {
  const { prefs, router } = ctx;
  const known = (item: RecentRecord) => models.find(model => model.name === item.model);

  const open = (button: HTMLElement) => openPopover(button, close => {
    const clock = minuteClock();
    const row = (item: RecentRecord) => {
      const model = known(item)!;
      const { icon, tint } = identity(model);
      const when = typeof item.at === 'number' && Number.isFinite(item.at) ? new Date(item.at) : null;
      const time = when ? html`<time class="rec-at" datetime=${when.toISOString()}>${() => relativeTime(when, clock.get())}</time>` as HTMLElement : null;
      if (time && when) tooltip(time, () => exactTime(when));
      const label = () => [item.title, model.label, when ? `opened ${relativeTime(when, clock.get())}` : ''].filter(Boolean).join(', ');
      return html`<a class="rec-item" role="menuitem" tabindex="-1" aria-label=${label}
        href=${router.href('record', { model: item.model, id: String(item.id) })} on:click=${() => close()}>
        ${Icon(icon, { size: 'sm', tint })}<span class="rec-text"><span class="rec-title">${item.title}</span><span class="rec-model">${model.label}</span></span>${time}</a>`;
    };
    const items = () => prefs.recentRecords.get().filter(known);
    const body = html`<div class="rec-pop-body"><div class="rec-h"><b>Recent records</b>${() => items().length ? html`<button type="button" class="link rec-clear"
      on:click=${() => { prefs.recentRecords.set([]); button.focus(); close(); }}>Clear</button>` : null}</div>
      ${() => items().length ? html`<div class="rec-list" role="menu" aria-label="Recent records">${items().map(row)}</div>`
        : html`<p class="muted rec-empty">No recent records yet.</p>`}</div>` as HTMLElement;
    body.addEventListener('keydown', event => {
      const links = [...body.querySelectorAll<HTMLElement>('.rec-item')];
      const at = links.indexOf(document.activeElement as HTMLElement);
      const next = { ArrowDown: at + 1, ArrowUp: at < 0 ? links.length - 1 : at - 1, Home: 0, End: links.length - 1 }[event.key];
      if (next === undefined || !links.length) return;
      event.preventDefault();
      links[(next + links.length) % links.length].focus();
    });
    queueMicrotask(() => (body.querySelector<HTMLElement>('.rec-item') ?? body.querySelector<HTMLElement>('.rec-clear'))?.focus());
    return body;
  }, { role: 'dialog', label: 'Recent records', className: 'rec-pop' });

  const button = html`<button type="button" class="icon-btn rec-btn" aria-label="Recent records" aria-haspopup="dialog" aria-expanded="false"
    on:click=${(event: MouseEvent) => open(event.currentTarget as HTMLElement)}
    on:keydown=${(event: KeyboardEvent) => { if (event.key === 'ArrowDown' && button.getAttribute('aria-expanded') !== 'true') { event.preventDefault(); open(button); } }}>${Icon(SHELL_ICONS.history, { size: 'sm' })}</button>` as HTMLElement;
  tooltip(button, 'Recent records');
  return button;
}
