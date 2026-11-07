// The notification centre (app/inbox.ts): the bell's unread count, the panel along the right edge
// under the top bar, mark read on click, Go to only for records the person can open, closing on an
// outside click or Esc, and popups for new items while the panel is closed.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InboxButton } from '../app/inbox';
import { cleanup, render } from './ui-helpers';

afterEach(() => cleanup());

const now = Math.floor(Date.now() / 1000);
const mention = { id: 5, kind: 'mention', at: now - 60, read: false, actor: 'Omar', text: 'Omar mentioned you on Project Fit out',
  record: { model: 'project', id: '01abc', label: 'Project', title: 'Fit out' } };
const hidden = { id: 4, kind: 'mention', at: now - 3 * 86400, read: true, actor: 'Sara', text: "Sara mentioned you on a record you can't open", record: null };

function fakeContext(start: any[]) {
  let items = start;
  const read: Array<number[] | 'all'> = [];
  const unread = () => items.filter(item => !item.read).length;
  const api = {
    inboxCount: vi.fn(async () => unread()),
    inbox: vi.fn(async () => ({ items, total: items.length, unread: unread() })),
    inboxRead: vi.fn(async (ids: number[] | 'all') => {
      read.push(ids);
      items = items.map(item => ids === 'all' || ids.includes(item.id) ? { ...item, read: true } : item);
      return unread();
    }),
    request: vi.fn(() => new Promise(() => {})),
    url: (path: string) => path,
  };
  const router = { href: (_: string, params: Record<string, string>) => `/app/${params.model}/${params.id}` };
  return { ctx: { api, router } as any, read, add: (item: any) => { items = [item, ...items]; } };
}
const panel = () => document.querySelector<HTMLElement>('.ntf-panel');
const bell = (host: Element) => host.querySelector<HTMLButtonElement>('.inbox-btn')!;

describe('the notification centre', () => {
  it('shows the unread count and opens a panel grouped by day, with Go to only for records the person can open', async () => {
    const { ctx } = fakeContext([mention, hidden]);
    const host = render(() => InboxButton(ctx));
    await vi.waitFor(() => expect(host.querySelector('.inbox-count')?.textContent).toBe('1'));
    expect(bell(host).getAttribute('aria-label')).toBe('Notifications, 1 unread');
    bell(host).click();
    expect(bell(host).getAttribute('aria-expanded')).toBe('true');
    expect(panel()!.getAttribute('role')).toBe('dialog');
    await vi.waitFor(() => expect(document.querySelectorAll('.ntf-item')).toHaveLength(2));
    expect([...document.querySelectorAll('.ntf-gh')].map(h => h.textContent)).toEqual(['Today', 'Earlier']);
    const [first, second] = [...document.querySelectorAll('.ntf-item')];
    expect(first.classList.contains('unread')).toBe(true);
    expect(first.querySelector('a.ntf-go')!.getAttribute('href')).toBe('/app/project/01abc');
    expect(first.querySelector('a.ntf-go')!.getAttribute('aria-label')).toBe('Go to Fit out');
    expect(second.querySelector('a.ntf-go')).toBeNull();
    expect(second.textContent).toContain("a record you can't open");
  });

  it('marks an item read on a click anywhere on it and keeps the panel open', async () => {
    const { ctx, read } = fakeContext([mention, hidden]);
    const host = render(() => InboxButton(ctx));
    bell(host).click();
    await vi.waitFor(() => expect(document.querySelectorAll('.ntf-item')).toHaveLength(2));
    document.querySelector<HTMLButtonElement>('.ntf-item .ntf-main')!.click();
    expect(read).toEqual([[5]]);
    await vi.waitFor(() => expect(host.querySelector('.inbox-count')).toBeNull());
    expect(document.querySelector('.ntf-item.unread')).toBeNull();
    expect(panel()!.classList.contains('on')).toBe(true);
    // A read item is not sent again.
    document.querySelector<HTMLButtonElement>('.ntf-item .ntf-main')!.click();
    expect(read).toEqual([[5]]);
  });

  it('Go to marks the item read and closes the panel', async () => {
    const { ctx, read } = fakeContext([mention]);
    const host = render(() => InboxButton(ctx));
    bell(host).click();
    await vi.waitFor(() => expect(document.querySelector('a.ntf-go')).not.toBeNull());
    const go = document.querySelector<HTMLAnchorElement>('a.ntf-go')!;
    go.addEventListener('click', event => event.preventDefault());
    go.click();
    expect(read).toEqual([[5]]);
    expect(bell(host).getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on a click outside it or on Esc, which gives focus back to the bell', async () => {
    const { ctx } = fakeContext([mention]);
    const host = render(() => InboxButton(ctx));
    bell(host).click();
    expect(document.activeElement).toBe(panel());
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(bell(host).getAttribute('aria-expanded')).toBe('false');
    bell(host).click();
    // A click inside the panel keeps it open.
    panel()!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(bell(host).getAttribute('aria-expanded')).toBe('true');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(bell(host).getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(bell(host));
    // The bell again closes it too.
    bell(host).click();
    bell(host).click();
    expect(bell(host).getAttribute('aria-expanded')).toBe('false');
  });

  it('pops up a new item at the top right while the panel is closed; a click on it opens the panel', async () => {
    const { ctx, add } = fakeContext([hidden]);
    const host = render(() => InboxButton(ctx));
    await vi.waitFor(() => expect(ctx.api.inboxCount).toHaveBeenCalled());
    add({ ...mention, id: 6, at: Math.floor(Date.now() / 1000), text: 'Sara mentioned you on Project Handover' });
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(document.querySelectorAll('.ntf-pops .ntf-pop')).toHaveLength(1));
    const pop = document.querySelector('.ntf-pops .ntf-pop')!;
    expect(pop.textContent).toContain('Sara mentioned you on Project Handover');
    expect(pop.querySelector('a.ntf-go')!.getAttribute('href')).toBe('/app/project/01abc');
    expect(document.querySelector('.ntf-pops')!.getAttribute('aria-live')).toBe('polite');
    // The old, read item never pops up.
    expect(document.querySelector('.ntf-pops')!.textContent).not.toContain("can't open");
    pop.querySelector<HTMLButtonElement>('.ntf-main')!.click();
    expect(bell(host).getAttribute('aria-expanded')).toBe('true');
    expect(document.querySelectorAll('.ntf-pops .ntf-pop')).toHaveLength(0);
  });
});
