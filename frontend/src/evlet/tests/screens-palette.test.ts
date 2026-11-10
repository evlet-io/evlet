import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, press, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, id, type Sent } from './server';

afterEach(stopAll);

const empty = { status: 200, etag: null, body: { items: [], total: 0, limit: 5, offset: 0 } };
const search = (sent: Sent) => new URL(sent.url, 'http://x').searchParams.get('q') === 'medical' ? copy(screen.customers_search) : copy(empty);
const routes = (): Route[] => [...adminRoutes(),
  ['GET', /\/api\/customer\?/, search], ['GET', /\/api\/(product|order|contact)\?/, empty],
  ['GET', /\/api\/customer\/000000000000s$/, { status: 200, etag: '"c25"', body: { id: id(25), title: 'Zayed Medical Supplies', rate: '34.50', credit_days: 39 } }]];
const palette = () => document.querySelector<HTMLElement>('.modal.palette');
const input = () => palette()!.querySelector<HTMLInputElement>('.pal-in input')!;

describe('command palette', () => {
  it('opens with Ctrl K on quick access, moves through the grid with arrow keys and opens a list', async () => {
    const { host } = start(routes());
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    press(document.body, 'k', { ctrlKey: true });
    expect(palette()).not.toBeNull();
    expect(document.activeElement).toBe(input());
    const tiles = [...palette()!.querySelectorAll('.qa-tile')].map(text);
    expect(tiles).toEqual(['Customers', 'New customer', 'Products', 'New product', 'Orders', 'New order']);
    expect(text(palette()!.querySelector('.qa-tile.on'))).toBe('Customers');
    press(input(), 'ArrowRight');
    press(input(), 'ArrowRight');
    expect(text(palette()!.querySelector('.qa-tile.on'))).toBe('Products');
    press(input(), 'Enter');
    await until(() => expect(location.pathname).toBe('/app/product'));
    expect(palette()).toBeNull();
  });

  it('searches records through each model’s list search, remembers the search and opens the record', async () => {
    const { sent } = start(routes());
    await until(() => expect(document.querySelector('.app')).not.toBeNull());
    click(document.querySelector('.top-search .ts-search'));
    typeInto(input(), 'medical');
    await until(() => expect([...palette()!.querySelectorAll('.pal-sec[aria-label="Records"] .pal-it .lbl')].map(text)).toEqual(['Zayed Medical Supplies', 'Bayan Medical']));
    // Only models with search fields are searched, five results each.
    const searched = requests(sent, 'GET', /q=medical/).map(item => new URL(item.url, 'http://x'));
    expect(searched.map(url => url.pathname).sort()).toEqual(['/api/contact', '/api/customer', '/api/order', '/api/product', '/api/quote']);
    expect(searched.every(url => url.searchParams.get('limit') === '5')).toBe(true);
    press(input(), 'Enter');
    await until(() => expect(location.pathname).toBe('/app/customer/000000000000s'));

    press(document.body, 'k', { metaKey: true });
    expect([...palette()!.querySelectorAll('.pal-sec')][0].querySelector('.pal-sh')!.textContent).toContain('Recent');
    expect(text(palette()!.querySelector('.pal-it .lbl'))).toBe('medical');
    press(input(), 'k', { metaKey: true });
    expect(palette()).toBeNull();
  });

  it('finds pages and preferences and says when nothing matches', async () => {
    start(routes());
    await until(() => expect(document.querySelector('.app')).not.toBeNull());
    press(document.body, 'k', { ctrlKey: true });
    typeInto(input(), 'sidebar');
    await until(() => expect(text(palette()!.querySelector('.pal-sec[aria-label="Preferences"] .lbl'))).toBe('Toggle sidebar'));
    press(input(), 'Enter');
    expect(document.querySelector('.app')!.classList.contains('collapsed')).toBe(true);
    press(document.body, 'k', { ctrlKey: true });
    typeInto(input(), 'new order');
    await until(() => expect(text(palette()!.querySelector('.pal-sec[aria-label="Pages and actions"] .pal-it'))).toBe('New orderCreate'));
    typeInto(input(), 'zzzz');
    await until(() => expect(text(palette()!.querySelector('.pal-empty'))).toContain('No results for “zzzz”'));
  });

  it('customizes quick access per user: rows, removing and adding tiles', async () => {
    start(routes());
    await until(() => expect(document.querySelector('.app')).not.toBeNull());
    press(document.body, 'k', { ctrlKey: true });
    click(byText(palette()!, '.pal-link', 'Customize'));
    expect(palette()!.querySelectorAll('.qa-tile .qa-rm')).toHaveLength(6);
    click(palette()!.querySelector('[aria-label="Remove Customers"]'));
    expect(palette()!.querySelectorAll('.qa-tile.empty')).toHaveLength(1);
    click(byText(palette()!, '.qa-chip', 'Docs'));
    click(byText(palette()!, '.seg button', '2 rows'));
    expect(palette()!.querySelectorAll('.qa-tile.empty')).toHaveLength(6);
    click(byText(palette()!, 'button', 'Done'));
    expect([...palette()!.querySelectorAll('.qa-tile')].map(text)).toEqual(['New customer', 'Products', 'New product', 'Orders', 'New order', 'Docs']);
  });
});
