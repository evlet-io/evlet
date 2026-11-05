// The person's inbox in the top bar: mentions, with an unread count kept live by the server's event
// stream (docs/jobs-realtime.md), and a regular check when the stream is unavailable. Each row links
// to its record only when the person can open it; otherwise it says only that they were mentioned.

import { html, listen, when } from '../dom';
import { cell, onCleanup } from '../reactive';
import { Icon } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { openPopover } from '../ui/layers';
import { formatDateTime } from '../model/format';
import type { AppContext } from './context';
import type { InboxItem } from './client';

const POLL_MS = 60000;

export function InboxButton(ctx: AppContext): HTMLElement {
  const unread = cell(0), items = cell<InboxItem[] | null>(null), problem = cell('');
  const refresh = () => ctx.api.inboxCount().then(count => unread.set(count), () => {});
  void refresh();

  // Live: the event stream sends {"type": "inbox.changed"} to this person only.
  let source: EventSource | null = null, timer = 0;
  const poll = () => { window.clearInterval(timer); timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void refresh(); }, POLL_MS); };
  if (typeof EventSource === 'function') {
    void ctx.api.request<{ cursor: string }>('GET', '/api/_events').then(({ data }) => {
      source = new EventSource(ctx.api.url(`/api/_events/stream?cursor=${encodeURIComponent(data.cursor)}`), { withCredentials: true });
      source.addEventListener('evlet', event => {
        try {
          const page = JSON.parse((event as MessageEvent).data) as { items?: Array<{ type?: string }> };
          if (page.items?.some(item => item.type === 'inbox.changed')) void refresh().then(() => { if (items.peek()) void load(); });
        } catch { /* a heartbeat or a page without inbox news */ }
      });
      source.addEventListener('error', () => { if (source?.readyState === EventSource.CLOSED) poll(); });
    }, poll);
  } else poll();
  onCleanup(() => { source?.close(); window.clearInterval(timer); });
  listen(window, 'focus', () => void refresh());

  const load = () => ctx.api.inbox({ limit: 30 }).then(page => { items.set(page.items); unread.set(page.unread); problem.set(''); },
    () => problem.set('Your mentions could not be loaded.'));

  const open = (button: HTMLElement) => {
    void load();
    openPopover(button, close => html`<div class="inbox"><div class="inbox-h"><b>Mentions</b>${when(() => unread.get() > 0, () => html`<button type="button" class="link"
      on:click=${() => void ctx.api.inboxRead('all').then(count => { unread.set(count); void load(); })}>Mark all as read</button>`)}</div>
      ${() => problem.get() ? html`<p class="muted inbox-empty">${problem.get()}</p>` : items.get() === null ? html`<p class="muted inbox-empty">Loading…</p>`
        : !items.get()!.length ? html`<p class="muted inbox-empty">No mentions yet. When someone mentions you in a note, it shows here.</p>`
        : html`<ul class="inbox-list">${items.get()!.map(item => row(item, close))}</ul>`}</div>`,
      { role: 'dialog', label: 'Mentions', className: 'inbox-pop' });
  };

  const row = (item: InboxItem, close: () => void) => {
    const read = () => { if (!item.read) void ctx.api.inboxRead([item.id]).then(count => unread.set(count)); };
    const when_ = formatDateTime(new Date(item.at * 1000));
    const body = html`<span class="inbox-text">${item.text}</span><span class="muted inbox-at">${when_}</span>`;
    return html`<li class="inbox-item" class:unread=${!item.read}>${item.record
      ? html`<a href=${ctx.router.href('record', { model: item.record.model, id: item.record.id })} on:click=${() => { read(); close(); }}>${Icon('at-sign', { size: 'sm' })}${body}</a>`
      : html`<button type="button" class="inbox-plain" on:click=${read}>${Icon('at-sign', { size: 'sm' })}${body}</button>`}</li>`;
  };

  const button = html`<button type="button" class="icon-btn inbox-btn" aria-haspopup="dialog"
    aria-label=${() => unread.get() ? `Mentions, ${unread.get()} unread` : 'Mentions'}
    on:click=${(event: MouseEvent) => open(event.currentTarget as HTMLElement)}>${Icon('bell')}${when(() => unread.get() > 0,
      () => html`<span class="inbox-count" aria-hidden="true">${() => unread.get() > 99 ? '99+' : String(unread.get())}</span>`)}</button>` as HTMLElement;
  tooltip(button, 'Mentions');
  return button;
}
