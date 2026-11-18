// Related records on a record page: a section after more information. Its chips name each relation at once; one read
// counts them all once the section shows, with a placeholder in each badge until then. Opened, each relation lists a
// page of five when its list shows, a small pager steps through the rest, and View all opens the related list
// filtered to this record.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, click, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
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
const counted = (count: number) => ({ status: 200, etag: null, body: { counts: { 'order.customer': count, 'quote.customer': 0 } } });
const routes = (count = 12, counts: Route[2] = counted(count)): Route[] => [...adminRoutes(), ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/customer\/0000000000001\/related$/, counts],
  ['GET', /\/api\/customer\/0000000000001\/related\/order\.customer/, page(orders.slice(0, count))],
  ['GET', /\/api\/customer\/0000000000001\/related\/quote\.customer/, page([])],
  ['GET', /\/api\/order\?/, screen.orders_open]];
const related = (sent: Sent[]) => requests(sent, 'GET', /\/related\//).map(item => item.url.replace(/^.*\/related\//, ''));
const section = (host: Element) => host.querySelector('.ev-related[aria-label="Orders"]')!;
const titles = (host: Element) => [...section(host).querySelectorAll('.ev-row-link')].map(text);
const relatedRow = (host: Element) => host.querySelector<HTMLElement>('.ev-rel[data-section="Related"]');
const counts = (sent: Sent[]) => requests(sent, 'GET', /\/related$/).length;
const chips = (host: Element) => [...host.querySelectorAll('.ev-rel-chip')].map(chip => chip.getAttribute('aria-label'));
/** Open the Related section (it starts closed). */
async function openRelated(host: Element) {
  await until(() => expect(relatedRow(host)).not.toBeNull());
  click(relatedRow(host)!.querySelector('.ev-rel-toggle'));
}

describe('related records on a record page', () => {
  it('names the relations at once, counts them in one read once the section shows, and lists rows only when opened', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const { host, sent } = start(routes(), '/app/customer/0000000000001');
    await until(() => expect(relatedRow(host)).not.toBeNull());
    // A grey section heading, as more information has, after it; closed, a chip per relation with a placeholder badge.
    expect(relatedRow(host)!.querySelector('h2.ev-more-label .ev-rel-toggle')?.getAttribute('aria-expanded')).toBe('false');
    expect(chips(host)).toEqual(['Orders: loading', 'Quotes: loading']);
    expect(relatedRow(host)!.querySelectorAll('.ev-rel-n .ev-sk')).toHaveLength(2);
    expect(counts(sent)).toBe(0);
    scrollIntoView();
    await until(() => expect(chips(host)).toEqual(['Orders: 12', 'Quotes: 0']));
    expect([...host.querySelectorAll('.ev-rel-n')].map(text)).toEqual(['12', '0']);
    expect(host.querySelector('.ev-rel-chip.is-zero')?.getAttribute('aria-label')).toBe('Quotes: 0');
    // One read for every count, and no rows yet.
    expect(counts(sent)).toBe(1);
    expect(related(sent)).toEqual([]);
    // Opened, each relation asks for its first page once its list shows.
    click(relatedRow(host)!.querySelector('.ev-rel-toggle'));
    expect(relatedRow(host)!.querySelector('.ev-rel-chips')!.hasAttribute('hidden')).toBe(true);
    scrollIntoView();
    await until(() => expect(titles(host)).toEqual(['Order 1', 'Order 2', 'Order 3', 'Order 4', 'Order 5']));
    expect(related(sent).sort()).toEqual(['order.customer?limit=5&offset=0', 'quote.customer?limit=5&offset=0']);
    expect(counts(sent)).toBe(1);
  });

  it('opens the section from a chip, and shows a dash when the counts cannot be read', async () => {
    const { host } = start(routes(12, { status: 500, etag: null, body: { error: 'Broken' } }), '/app/customer/0000000000001');
    await until(() => expect(chips(host)).toEqual(['Orders: not loaded', 'Quotes: not loaded']));
    expect([...host.querySelectorAll('.ev-rel-n')].map(text)).toEqual(['–', '–']);
    click(host.querySelector('.ev-rel-chip'));
    expect(relatedRow(host)!.querySelector('.ev-rel-toggle')!.getAttribute('aria-expanded')).toBe('true');
    // The page's total stands in for the count it could not read.
    await until(() => expect(titles(host)).toHaveLength(5));
    click(relatedRow(host)!.querySelector('.ev-rel-toggle'));
    await until(() => expect(chips(host)[0]).toBe('Orders: 12'));
  });

  it('disconnects hidden related observers when leaving a record', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const { host, router } = start(routes(), '/app/customer/0000000000001');
    await until(() => expect(relatedRow(host)).not.toBeNull());
    expect(watched.size).toBeGreaterThan(0);
    await router.navigate('/docs');
    await until(() => expect(host.querySelector('.ev-record')).toBeNull());
    expect(watched.size).toBe(0);
  });

  it('loads a Related section only once it scrolls into view', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const { host, sent } = start(routes(), '/app/customer/0000000000001');
    await openRelated(host);
    await until(() => expect(section(host)).not.toBeNull());
    expect(related(sent)).toEqual([]);
    scrollIntoView();
    await until(() => expect(titles(host)).toHaveLength(5));
  });

  it('shows five rows with a pager that steps through the rest by server pages', async () => {
    const { host, sent } = start(routes(), '/app/customer/0000000000001');
    await openRelated(host);
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
    const { host } = start(routes(5), '/app/customer/0000000000001');
    await openRelated(host);
    await until(() => expect(titles(host)).toHaveLength(5));
    expect(section(host).querySelector('.ev-pager')).toBeNull();
    const quotes = host.querySelector('.ev-related[aria-label="Quotes"]')!;
    await until(() => expect(text(quotes.querySelector('.ev-related-empty'))).toBe('No quotes yet.'));
    expect(quotes.querySelector('.ev-view-all')).toBeNull();
  });

  it('View all opens the related list filtered to this record, as a filter that can be removed', async () => {
    const { host, sent } = start(routes(), '/app/customer/0000000000001');
    await openRelated(host);
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

  it('reloads the counts after a save once they were read, and not before', async () => {
    let reads = 0;
    const version = cell('"1"');
    const pages = relatedPages([{ name: 'a', label: 'A', model: 'x', link: 'y', limit: 10 }], async () => ({ items: [], total: 0 }), version, () => 'Not loaded.',
      async () => ({ a: ++reads }));
    version.set('"2"');
    await Promise.resolve();
    expect(reads).toBe(0);
    pages.ensureCounts();
    pages.ensureCounts();
    await until(() => expect(pages('a').count.peek()).toBe(1));
    version.set('"3"');
    await until(() => expect(pages('a').count.peek()).toBe(2));
    expect(reads).toBe(2);
  });
});
