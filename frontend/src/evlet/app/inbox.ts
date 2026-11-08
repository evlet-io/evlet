// The person's notification centre: a bell in the top bar with the unread count, kept live by the
// server's event stream (docs/jobs-realtime.md) and a regular check when the stream is unavailable.
// The bell opens a panel along the right edge, under the top bar; a click anywhere else, Esc or the
// bell again slides it back. New items arrive as short popups at the top right while it is closed.
// A click on an item marks it read; Go to, shown only when the person can open its record, opens it.

import { html, listen, when } from '../dom';
import { cell, onCleanup, runWithOwner, scope } from '../reactive';
import { Icon } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { exactTime, minuteClock, relativeTime } from '../ui/time';
import type { AppContext } from './context';
import type { InboxItem } from './client';

const POLL_MS = 60000;
const POPUP_MS = 6000;
const POPUP_MAX = 3;

const KIND_ICON: Record<string, string> = { mention: 'at-sign' };
const iconOf = (item: InboxItem) => KIND_ICON[item.kind] ?? 'bell';

/** Today, or earlier, in the person's own day. */
function groupOf(at: number, now = new Date()) {
  const day = new Date(at * 1000);
  return day.toDateString() === now.toDateString() ? 'Today' : 'Earlier';
}

export function InboxButton(ctx: AppContext): HTMLElement {
  const unread = cell(0), items = cell<InboxItem[] | null>(null), problem = cell(''), open = cell(false);
  const clock = minuteClock();
  // Items newer than this id pop up; until the first list, anything unread since the page loaded does.
  let newest: number | null = null;
  const since = Math.floor(Date.now() / 1000) - 5;

  const load = () => ctx.api.inbox({ limit: 30 }).then(page => {
    const fresh = page.items.filter(item => !item.read && (newest === null ? item.at >= since : item.id > newest));
    newest = Math.max(newest ?? 0, ...page.items.map(item => item.id));
    items.set(page.items); unread.set(page.unread); problem.set('');
    return fresh;
  }, () => { problem.set('Your notifications could not be loaded.'); return [] as InboxItem[]; });

  const refresh = (news = false) => ctx.api.inboxCount().then(count => {
    const grew = count > unread.peek();
    unread.set(count);
    if (news || grew || open.peek()) void load().then(fresh => { if (!open.peek()) fresh.slice(0, POPUP_MAX).reverse().forEach(popup); });
  }, () => {});
  void ctx.api.inboxCount().then(count => unread.set(count), () => {});

  // Live: the event stream sends {"type": "inbox.changed"} to this person only.
  let source: EventSource | null = null, timer = 0;
  const poll = () => { window.clearInterval(timer); timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void refresh(); }, POLL_MS); };
  if (typeof EventSource === 'function') {
    void ctx.api.request<{ cursor: string }>('GET', '/api/_events').then(({ data }) => {
      source = new EventSource(ctx.api.url(`/api/_events/stream?cursor=${encodeURIComponent(data.cursor)}`), { withCredentials: true });
      source.addEventListener('evlet', event => {
        try {
          const page = JSON.parse((event as MessageEvent).data) as { items?: Array<{ type?: string }> };
          if (page.items?.some(item => item.type === 'inbox.changed')) void refresh(true);
        } catch { /* a heartbeat or a page without inbox news */ }
      });
      source.addEventListener('error', () => { if (source?.readyState === EventSource.CLOSED) poll(); });
    }, poll);
  } else poll();
  listen(window, 'focus', () => void refresh());

  const markRead = (item: InboxItem) => {
    if (item.read) return;
    items.set((items.peek() ?? []).map(row => row.id === item.id ? { ...row, read: true } : row));
    void ctx.api.inboxRead([item.id]).then(count => unread.set(count), () => {});
  };
  const markAll = () => {
    items.set((items.peek() ?? []).map(row => ({ ...row, read: true })));
    void ctx.api.inboxRead('all').then(count => unread.set(count), () => {});
  };
  const hrefOf = (item: InboxItem) => item.record ? ctx.router.href('record', { model: item.record.model, id: item.record.id }) : null;

  const time = (item: InboxItem) => {
    const at = new Date(item.at * 1000);
    const node = html`<time class="ntf-at" datetime=${at.toISOString()}>${() => relativeTime(at, clock.get())}</time>` as HTMLElement;
    tooltip(node, () => exactTime(at));
    return node;
  };
  const goTo = (item: InboxItem, after: () => void) => {
    const href = hrefOf(item);
    return href ? html`<a class="btn sm ntf-go" href=${href} aria-label=${`Go to ${item.record!.title || item.record!.label}`}
      on:click=${(event: MouseEvent) => { event.stopPropagation(); markRead(item); after(); }}>Go to${Icon('arrow-right', { size: 'xs' })}</a>` : null;
  };

  // ---------- the panel ----------

  let panel: { node: HTMLElement; scrim: HTMLElement; dispose: () => void } | null = null;
  // A panel still sliding out is removed at once if the bell opens a new one.
  let leaving: (() => void) | null = null;
  const close = (restore = true) => {
    if (!panel) return;
    const { node, scrim, dispose } = panel, inside = node.contains(document.activeElement);
    panel = null;
    open.set(false);
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', escape, true);
    window.removeEventListener('popstate', closeQuiet);
    node.classList.remove('on'); scrim.classList.remove('on');
    const gone = () => { if (leaving === gone) leaving = null; dispose(); node.remove(); scrim.remove(); };
    leaving = gone;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) gone(); else window.setTimeout(() => { if (node.isConnected) gone(); }, 220);
    if (restore && (inside || document.activeElement === document.body)) button.focus();
  };
  const closeQuiet = () => close(false);
  const outside = (event: Event) => {
    const target = event.target as Node;
    if (panel && !panel.node.contains(target) && !button.contains(target) && !(target as Element).closest?.('.ntf-pops')) close(false);
  };
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !panel) return;
    event.preventDefault(); event.stopPropagation();
    close(true);
  };

  const row = (item: InboxItem) => html`<li class="ntf-item" class:unread=${!item.read}>
    <button type="button" class="ntf-main" aria-label=${`${item.text}, ${relativeTime(new Date(item.at * 1000))}${item.read ? '' : ', unread'}`} on:click=${() => markRead(item)}>
      <span class="ntf-ic" aria-hidden="true">${Icon(iconOf(item), { size: 'sm' })}</span>
      <span class="ntf-body"><span class="ntf-text">${item.text}</span>${time(item)}</span></button>
    ${goTo(item, () => close(false))}</li>`;

  const list = () => {
    if (problem.get()) return html`<p class="muted ntf-empty">${problem.get()}</p>`;
    const rows = items.get();
    if (rows === null) return html`<p class="muted ntf-empty">Loading…</p>`;
    if (!rows.length) return html`<p class="muted ntf-empty">You're all caught up. Mentions and other news for you show here.</p>`;
    const now = new Date(clock.get());
    return (['Today', 'Earlier'] as const).map(group => {
      const shown = rows.filter(item => groupOf(item.at, now) === group);
      return shown.length ? html`<section class="ntf-group" aria-label=${group}><h3 class="ntf-gh">${group}</h3><ul class="ntf-list">${shown.map(row)}</ul></section>` : null;
    });
  };

  const show = () => {
    if (panel) return;
    leaving?.();
    void load();
    let node!: HTMLElement;
    const dispose = runWithOwner(null, () => scope(() => {
      node = html`<aside class="ntf-panel" id="ev-notifications" role="dialog" aria-modal="false" aria-label="Notifications" tabindex="-1">
        <div class="ntf-h"><h2>Notifications</h2>${when(() => unread.get() > 0, () => html`<button type="button" class="link" on:click=${markAll}>Mark all as read</button>`)}
          <button type="button" class="icon-btn" aria-label="Close notifications" on:click=${() => close(true)}>${Icon('x', { size: 'sm' })}</button></div>
        <div class="ntf-scroll">${list}</div></aside>` as HTMLElement;
    }));
    const scrim = html`<div class="ntf-scrim" aria-hidden="true"></div>` as HTMLElement;
    document.body.append(scrim, node);
    panel = { node, scrim, dispose };
    open.set(true);
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    window.addEventListener('popstate', closeQuiet);
    void node.offsetWidth; // start the slide from off screen
    node.classList.add('on'); scrim.classList.add('on');
    node.focus({ preventScroll: true });
    clearPopups();
  };

  // ---------- popups ----------

  let pops: HTMLElement | null = null;
  const region = () => {
    if (pops?.isConnected) return pops;
    pops = html`<div class="ntf-pops" role="region" aria-label="New notifications" aria-live="polite"></div>` as HTMLElement;
    document.body.append(pops);
    return pops;
  };
  const clearPopups = () => pops?.querySelectorAll('.ntf-pop').forEach(node => node.remove());
  function popup(item: InboxItem) {
    let node!: HTMLElement, timer = 0;
    const dispose = runWithOwner(null, () => scope(() => {
      node = html`<div class="ntf-pop">
        <button type="button" class="ntf-main" on:click=${() => { dismiss(); show(); }}>
          <span class="ntf-ic" aria-hidden="true">${Icon(iconOf(item), { size: 'sm' })}</span>
          <span class="ntf-body"><span class="ntf-text">${item.text}</span>${time(item)}</span></button>
        <span class="ntf-pop-acts">${goTo(item, () => dismiss())}
          <button type="button" class="icon-btn" aria-label="Dismiss" on:click=${() => dismiss()}>${Icon('x', { size: 'xs' })}</button></span></div>` as HTMLElement;
    }));
    const dismiss = () => {
      window.clearTimeout(timer);
      node.classList.add('out');
      window.setTimeout(() => { dispose(); node.remove(); }, 200);
    };
    const start = () => { window.clearTimeout(timer); timer = window.setTimeout(dismiss, POPUP_MS); };
    node.addEventListener('pointerenter', () => window.clearTimeout(timer));
    node.addEventListener('pointerleave', start);
    node.addEventListener('focusin', () => window.clearTimeout(timer));
    node.addEventListener('focusout', start);
    const host = region();
    host.prepend(node);
    while (host.children.length > POPUP_MAX) host.lastElementChild!.remove();
    start();
  }

  onCleanup(() => { source?.close(); window.clearInterval(timer); close(false); leaving?.(); pops?.remove(); });

  const button = html`<button type="button" class="icon-btn inbox-btn" aria-haspopup="dialog" aria-controls="ev-notifications"
    aria-expanded=${() => String(open.get())}
    aria-label=${() => unread.get() ? `Notifications, ${unread.get()} unread` : 'Notifications'}
    on:click=${() => panel ? close(true) : show()}>${Icon('bell')}${when(() => unread.get() > 0,
      () => html`<span class="inbox-count" aria-hidden="true">${() => unread.get() > 99 ? '99+' : String(unread.get())}</span>`)}</button>` as HTMLElement;
  tooltip(button, 'Notifications');
  return button;
}
