// Related records on a record page: nothing is asked for until the Related block or a Related tab
// section shows, one page of five per relation serves both, a small pager steps through the rest,
// and View all opens the related list filtered to this record.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import type { Sent } from './server';
import { cell } from '../reactive';
import { relatedPages } from '../app/related-data';

// A stand-in IntersectionObserver whose elements come into view only when the test says so.
const watched = new Set<{ show(): void }>();
class FakeObserver {
  private elements: Element[] = [];
  constructor(private callback: IntersectionObserverCallback) {}
  observe(element: Element) { this.elements.push(element); watched.add(this); }
  disconnect() { watched.delete(this); }
  show() { this.callback(this.elements.map(target => ({ target, isIntersecting: true }) as IntersectionObserverEntry), this as never); }
}
const scrollIntoView = () => [...watched].forEach(observer => observer.show());

afterEach(() => { vi.unstubAllGlobals(); watched.clear(); stopAll(); });

const template = screen.orders_open.body.items[0];
const orders = Array.from({ length: 12 }, (_, index) => ({ ...template, id: `00000000000${String(index + 10).padStart(2, '0')}`, title: `Order ${index + 1}` }));
const page = (items: unknown[]) => (sent: Sent) => {
  const query = new URL(sent.url, 'http://127.0.0.1').searchParams;
  const limit = Number(query.get('limit')), offset = Number(query.get('offset') ?? 0);
  return { status: 200, etag: null, body: { items: items.slice(offset, offset + limit), total: items.length, limit, offset } };
};
const routes = (count = 12): Route[] => [...adminRoutes(), ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/customer\/0000000000001\/related\/order\.customer/, page(orders.slice(0, count))],
  ['GET', /\/api\/customer\/0000000000001\/related\/quote\.customer/, page([])],
  ['GET', /\/api\/order\?/, screen.orders_open]];
const related = (sent: Sent[]) => requests(sent, 'GET', /\/related\//).map(item => item.url.replace(/^.*\/related\//, ''));
const section = (host: Element) => host.querySelector('.ev-related[aria-label="Orders"]')!;
const titles = (host: Element) => [...section(host).querySelectorAll('.ev-row-link')].map(text);

describe('related records on a record page', () => {
  it('asks for nothing until the Related block shows, then one page of five per relation, kept across tabs', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const { host, sent } = start(routes(), '/app/customer/0000000000001');
    await until(() => expect(byText(host, '.doc-tabs [role="tab"]', 'Related')).not.toBeNull());
    await until(() => expect(host.querySelector('.rel')).not.toBeNull());
    expect(related(sent)).toEqual([]);
    scrollIntoView();
    await until(() => expect(host.querySelector('.rel-row')?.getAttribute('aria-label')).toBe('Orders, 12 orders'));
    expect(related(sent).sort()).toEqual(['order.customer?limit=5&offset=0', 'quote.customer?limit=5&offset=0']);
    // The Related tab shows the pages already loaded: no request when it opens or scrolls into view.
    click(byText(host, '.doc-tabs [role="tab"]', 'Related'));
    scrollIntoView();
    await until(() => expect(titles(host)).toEqual(['Order 1', 'Order 2', 'Order 3', 'Order 4', 'Order 5']));
    expect(related(sent)).toHaveLength(2);
  });

  it('loads a Related tab section only once it scrolls into view', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const { host, sent } = start(routes(), '/app/customer/0000000000001?tab=related');
    await until(() => expect(section(host)).not.toBeNull());
    expect(related(sent)).toEqual([]);
    scrollIntoView();
    await until(() => expect(titles(host)).toHaveLength(5));
  });

  it('shows five rows with a pager that steps through the rest by server pages', async () => {
    const { host, sent } = start(routes(), '/app/customer/0000000000001?tab=related');
    await until(() => expect(titles(host)).toEqual(['Order 1', 'Order 2', 'Order 3', 'Order 4', 'Order 5']));
    const pager = () => section(host).querySelector('.ev-pager')!;
    const [previous, next] = [...pager().querySelectorAll<HTMLButtonElement>('button')];
    expect(text(pager().querySelector('.num'))).toBe('1–5 of 12');
    expect(previous.disabled).toBe(true);
    click(next);
    await until(() => expect(titles(host)).toEqual(['Order 6', 'Order 7', 'Order 8', 'Order 9', 'Order 10']));
    expect(text(pager().querySelector('.num'))).toBe('6–10 of 12');
    click(next);
    await until(() => expect(titles(host)).toEqual(['Order 11', 'Order 12']));
    expect(text(pager().querySelector('.num'))).toBe('11–12 of 12');
    expect(next.disabled).toBe(true);
    expect(previous.disabled).toBe(false);
    expect(related(sent).filter(url => url.startsWith('order.'))).toEqual(['order.customer?limit=5&offset=0', 'order.customer?limit=5&offset=5', 'order.customer?limit=5&offset=10']);
  });

  it('has no pager for five rows or fewer, and keeps the empty line', async () => {
    const { host } = start(routes(5), '/app/customer/0000000000001?tab=related');
    await until(() => expect(titles(host)).toHaveLength(5));
    expect(section(host).querySelector('.ev-pager')).toBeNull();
    const quotes = host.querySelector('.ev-related[aria-label="Quotes"]')!;
    await until(() => expect(text(quotes.querySelector('.ev-related-empty'))).toBe('No quotes yet.'));
    expect(quotes.querySelector('.ev-view-all')).toBeNull();
  });

  it('View all opens the related list filtered to this record, as a filter that can be removed', async () => {
    const { host, sent } = start(routes(), '/app/customer/0000000000001?tab=related');
    await until(() => expect(section(host).querySelector('.ev-view-all')).not.toBeNull());
    const link = section(host).querySelector<HTMLAnchorElement>('.ev-view-all')!;
    const href = new URL(link.href, 'http://127.0.0.1');
    expect(href.pathname).toBe('/app/order');
    expect(JSON.parse(href.searchParams.get('f')!)).toEqual([['customer', 'eq', '0000000000001']]);
    click(link);
    await until(() => expect(location.pathname).toBe('/app/order'));
    await until(() => expect(host.querySelector('.filter-chip')).not.toBeNull());
    expect(text(host.querySelector('.filter-chip'))).toMatch(/^Customer/);
    const listed = requests(sent, 'GET', /\/api\/order\?/).pop()!;
    expect(JSON.parse(new URL(listed.url, 'http://127.0.0.1').searchParams.get('filters')!)).toEqual({ customer: '0000000000001' });
    click(host.querySelector('.filter-chip button'));
    await until(() => expect(host.querySelector('.filter-chip')).toBeNull());
    expect(new URLSearchParams(location.search).get('f')).toBeNull();
  });

  it('reloads only the pages already shown after a save, at the page they were on', async () => {
    const asked: string[] = [];
    const version = cell('"1"');
    const pages = relatedPages([{ name: 'a', label: 'A', model: 'x', link: 'y', limit: 10 }, { name: 'b', label: 'B', model: 'x', link: 'y', limit: 10 }],
      async (name, { offset }) => { asked.push(`${name}@${offset}`); return { items: [], total: 0 }; }, version, () => 'Not loaded.');
    pages('a').load(5);
    version.set('"2"');
    await Promise.resolve();
    expect(asked).toEqual(['a@5', 'a@5']);
  });
});
