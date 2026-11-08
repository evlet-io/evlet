import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, press, recorded, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';
import { activeShortcut, workspaceNav } from '../app/workspaces';

afterEach(stopAll);

// The recorded schema, with each model given to an installed app through its resource id, and reports. Without
// `workspaces`, as an older server sends it, so the sidebar builds the fallback.
function appsSchema() {
  const schema = copy(screen.schema);
  delete schema.body.workspaces;
  const apps: Record<string, string> = { customer: 'sales.Customer', order: 'sales.Order', quote: 'sales.Quote', product: 'stock.Product', contact: 'crm.Contact' };
  for (const model of schema.body.models) if (apps[model.name]) model.resource = apps[model.name];
  schema.body.models.find((model: any) => model.name === 'product').description = 'Things we sell, with their stock.';
  const reports = copy(screen.report_schema).body.reports;
  schema.body.reports = [...reports, { ...reports[0], name: 'StockLevels', label: 'Stock levels', model: 'product', group: '', description: 'Units on hand by warehouse.' }];
  return schema;
}
const routes = (schema = appsSchema()): Route[] => [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, schema],
  ['GET', /\/api\/(customer|product|contact|order|quote)\?/, screen.customers_page1],
];
function memory() {
  const storage = new Map<string, string>();
  return { storage, store: { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => void storage.set(key, value),
    removeItem: (key: string) => void storage.delete(key) } as unknown as Storage };
}
const sections = (host: HTMLElement) => [...host.querySelectorAll('.nav-sec')].map(group => [text(group.querySelector('.nav-sec-h .lbl')), [...group.querySelectorAll('.nav-sc .lbl')].map(text)]);

describe('workspaceNav without declared workspaces', () => {
  it('makes one workspace per app, with Documents, Records and Reports sections, and finds the active shortcut', () => {
    const nav = workspaceNav(appsSchema().body, { list: model => `/app/${model}`, report: name => `/reports/${name}` }, 'Evlet');
    expect(nav.workspaces.map(workspace => [workspace.key, workspace.label, workspace.sections.map(section => [section.label, section.items.map(item => item.href)])])).toEqual([
      ['sales', 'Sales', [['Documents', ['/app/order']], ['Records', ['/app/customer', '/app/quote']], ['Reports', ['/reports/CustomerRates', '/reports/RatesByTerms']]]],
      ['stock', 'Stock', [['Records', ['/app/product']], ['Reports', ['/reports/StockLevels']]]],
      ['crm', 'Crm', [['Records', ['/app/contact']]]],
    ]);
    // Child tables are never shortcuts; a record page belongs to its list's shortcut.
    expect(JSON.stringify(nav)).not.toContain('order_lines');
    expect(activeShortcut(nav, '/app/product/0000000000001?tab=main')?.item.label).toBe('Products');
    expect(activeShortcut(nav, '/reports/StockLevels')?.workspace.key).toBe('stock');
    expect(activeShortcut(nav, '/docs')).toBeNull();
    // An installed app's report name carries its app, whatever its model.
    const schema = appsSchema().body;
    schema.reports.push({ ...schema.reports[0], name: 'stock.Valuation', label: 'Valuation', model: 'customer' });
    expect(workspaceNav(schema, { list: model => `/app/${model}`, report: name => `/reports/${name}` }).workspaces[1].sections[1].items.map(item => item.label)).toEqual(['Stock levels', 'Valuation']);
  });
});

describe('the Workspace switcher', () => {
  it('opens a card below the top bar button, moves between tiles with the arrow keys, closes on Esc and opens a workspace', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(host.querySelector('.app-switch .as-text b')).not.toBeNull());
    const button = host.querySelector<HTMLElement>('.app-switch')!;
    // In the top bar, after the brand and the pipe; not in the sidebar.
    expect(button.parentElement!.matches('.topbar > .top-left')).toBe(true);
    expect(button.previousElementSibling!.classList.contains('top-pipe')).toBe(true);
    expect(host.querySelector('.sidebar .app-switch')).toBeNull();
    expect(text(button.querySelector('.as-text b'))).toBe('Sales');
    expect(button.getAttribute('aria-label')).toBe('Workspace: Sales. Switch workspace');
    button.getBoundingClientRect = () => ({ left: 120, right: 220, top: 12, bottom: 40, width: 100, height: 28, x: 120, y: 12, toJSON: () => ({}) });
    click(button);
    const card = document.querySelector<HTMLElement>('.pop.ws-pop')!;
    // Below the button, its left edge level with the button's.
    expect(card.classList.contains('pop-side')).toBe(false);
    expect([card.style.left, card.style.top]).toEqual(['120px', '46px']);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(text(card.querySelector('.ws-head'))).toBe('Workspace');
    const tiles = [...card.querySelectorAll<HTMLButtonElement>('.ws-tile')];
    expect(tiles.map(text)).toEqual(['Sales', 'Stock', 'Crm']);
    expect(tiles[0].getAttribute('aria-current')).toBe('true');
    await until(() => expect(document.activeElement).toBe(tiles[0]));
    press(tiles[0], 'ArrowRight');
    expect(document.activeElement).toBe(tiles[1]);
    press(tiles[1], 'End');
    expect(document.activeElement).toBe(tiles[2]);
    press(tiles[2], 'ArrowRight');
    expect(document.activeElement).toBe(tiles[0]);
    press(tiles[0], 'ArrowLeft');
    expect(document.activeElement).toBe(tiles[2]);
    press(document.activeElement!, 'Escape');
    expect(document.querySelector('.ws-pop')).toBeNull();
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute('aria-expanded')).toBe('false');

    click(button);
    click(byText(document.body, '.ws-tile', 'Stock'));
    await until(() => expect(location.pathname).toBe('/app/product'));
    expect(text(button.querySelector('.as-text b'))).toBe('Stock');
    expect(sections(host)).toEqual([['Records', ['Products']], ['Reports', ['Stock levels']]]);
  });

  it('keeps the card on screen when the button is near the right edge, as on a narrow window', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(host.querySelector('.app-switch .as-text b')).not.toBeNull());
    const button = host.querySelector<HTMLElement>('.app-switch')!;
    const card = () => document.querySelector<HTMLElement>('.pop.ws-pop')!;
    button.getBoundingClientRect = () => ({ left: window.innerWidth - 40, right: window.innerWidth - 4, top: 12, bottom: 40, width: 36, height: 28, x: window.innerWidth - 40, y: 12, toJSON: () => ({}) });
    click(button);
    expect(card().classList.contains('pop-side')).toBe(false);
    expect(card().style.top).toBe('46px');
    expect(parseFloat(card().style.left)).toBeLessThanOrEqual(window.innerWidth - 12 - card().offsetWidth);
    // A second click on the button closes it, as before.
    click(button);
    expect(document.querySelector('.ws-pop')).toBeNull();
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('the workspace sidebar', () => {
  it('shows the sections of the current workspace and highlights the open shortcut', async () => {
    const { host } = start(routes(), '/app/quote');
    await until(() => expect(host.querySelector('.nav-sc.active')).not.toBeNull());
    expect(sections(host)).toEqual([['Documents', ['Orders']], ['Records', ['Customers', 'Quotes']], ['Reports', ['Customer rates', 'Rates by terms']]]);
    const active = host.querySelector('.nav-sc.active')!;
    expect(text(active)).toBe('Quotes');
    expect(active.getAttribute('aria-current')).toBe('page');
    expect(host.querySelectorAll('.sidebar [aria-current="page"]').length).toBe(1);
    // Every shortcut starts with its kind's icon in place of a dot, in the open sidebar and the collapsed one.
    expect(active.querySelector('.nav-dot')).toBeNull();
    expect(active.querySelector('svg.nav-ic')).not.toBeNull();
    expect(active.getAttribute('data-kind')).toBe('list');
    // Opening a shortcut of another workspace shows that workspace.
    click(host.querySelector('a[href="/reports/CustomerRates"]'));
    await until(() => expect(location.pathname).toBe('/reports/CustomerRates'));
    expect(text(host.querySelector('.nav-sc.active'))).toBe('Customer rates');
  });

  it('marks the section that holds the open shortcut, closed or open and in the collapsed sidebar', async () => {
    const { host } = start(routes(), '/app/quote');
    await until(() => expect(host.querySelector('.nav-sc.active')).not.toBeNull());
    const marked = () => [...host.querySelectorAll('.nav-sec.on')].map(group => text(group.querySelector('.nav-sec-h .lbl')));
    expect(marked()).toEqual(['Records']);
    // Closing the section keeps its mark, so the person still sees where they are.
    const records = byText(host, '.nav-sec', /^Records/)!;
    click(records.querySelector('.nav-sec-h'));
    expect(records.classList.contains('open')).toBe(false);
    expect(marked()).toEqual(['Records']);
    // The collapsed sidebar keeps the mark and the active shortcut.
    click(host.querySelector('.side-edge'));
    await until(() => expect(host.querySelector('.app.collapsed')).not.toBeNull());
    expect(marked()).toEqual(['Records']);
    expect(text(host.querySelector('.nav-sc.active'))).toBe('Quotes');
    // Another page moves the mark to its section.
    click(host.querySelector('a[href="/app/order"]'));
    await until(() => expect(marked()).toEqual(['Documents']));
  });

  it('collapses a section and remembers it for the person', async () => {
    const { storage, store } = memory();
    const first = start(routes(), '/app/customer', { storage: store });
    await until(() => expect(first.host.querySelector('.nav-sec')).not.toBeNull());
    const records = byText(first.host, '.nav-sec', /^Records/)!;
    const head = records.querySelector<HTMLButtonElement>('.nav-sec-h')!;
    expect(records.classList.contains('open')).toBe(true);
    expect(head.getAttribute('aria-expanded')).toBe('true');
    expect(head.getAttribute('aria-controls')).toBe(records.querySelector('.nav-sec-items')!.id);
    click(head);
    expect(records.classList.contains('open')).toBe(false);
    expect(head.getAttribute('aria-expanded')).toBe('false');
    await until(() => expect(JSON.parse(storage.get('evlet.admin.nav.closed') ?? '{}')).toEqual({ 'sales.records': true }));
    stopAll();

    const again = start(routes(), '/app/customer', { storage: store });
    await until(() => expect(again.host.querySelector('.nav-sec')).not.toBeNull());
    expect(byText(again.host, '.nav-sec', /^Records/)!.classList.contains('open')).toBe(false);
    expect(byText(again.host, '.nav-sec', /^Documents/)!.classList.contains('open')).toBe(true);
    click(byText(again.host, '.nav-sec', /^Records/)!.querySelector('.nav-sec-h'));
    await until(() => expect(JSON.parse(storage.get('evlet.admin.nav.closed')!)).toEqual({}));
  });

  it('works with blocked storage', async () => {
    const blocked = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } } as unknown as Storage;
    const { host } = start(routes(), '/app/customer', { storage: blocked });
    await until(() => expect(host.querySelector('.nav-sec')).not.toBeNull());
    click(byText(host, '.nav-sec', /^Records/)!.querySelector('.nav-sec-h'));
    expect(byText(host, '.nav-sec', /^Records/)!.classList.contains('open')).toBe(false);
  });
});

describe('the Explorer', () => {
  it('opens from the top bar and lists every report with search and an app filter', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(host.querySelector('.top-search')).not.toBeNull());
    // The sidebar has no Explorer button any more.
    expect(byText(host, '.sidebar .nav-item', 'Explorer')).toBeNull();
    const button = host.querySelector<HTMLButtonElement>('.top-search .ts-explorer')!;
    expect(button.getAttribute('aria-pressed')).toBe('false');
    click(button);
    await until(() => expect(text(host.querySelector('.ev-explorer .ph h1'))).toBe('Explorer'));
    expect(location.search).toBe('?explorer=reports');
    expect(button.classList.contains('on')).toBe(true);
    expect(button.getAttribute('aria-pressed')).toBe('true');
    // Nothing in the sidebar is marked on the Explorer.
    expect(host.querySelectorAll('.sidebar .nav-item.active').length).toBe(0);
    const cards = () => [...host.querySelectorAll<HTMLAnchorElement>('.ex-card')].map(card => [text(card.querySelector('b')), text(card.querySelector('.ex-app')), card.getAttribute('href')]);
    expect(cards()).toEqual([['Customer rates', 'Sales', '/reports/CustomerRates'], ['Rates by terms', 'Sales', '/reports/RatesByTerms'], ['Stock levels', 'Stock', '/reports/StockLevels']]);
    expect(text(byText(host, '.ex-card', /^Stock levels/)!.querySelector('.ex-desc'))).toBe('Units on hand by warehouse.');
    // Search matches the name, the description and the app.
    const search = host.querySelector<HTMLInputElement>('.ev-explorer input[type="search"]')!;
    typeInto(search, 'terms');
    expect(host.querySelector('.ev-explorer input[type="search"]')).toBe(search);
    expect(cards().map(card => card[0])).toEqual(['Rates by terms']);
    typeInto(search, 'warehouse');
    expect(cards().map(card => card[0])).toEqual(['Stock levels']);
    typeInto(search, 'sales');
    expect(cards().map(card => card[0])).toEqual(['Customer rates', 'Rates by terms']);
    typeInto(search, 'nothing like this');
    expect(text(host.querySelector('.ev-explorer .empty, .ev-explorer [class*="empty"]'))).toContain('Nothing matches');
    typeInto(search, '');
    const filter = host.querySelector<HTMLSelectElement>('.ex-app-filter')!;
    expect([...filter.options].map(option => option.textContent)).toEqual(['All apps', 'Sales', 'Stock', 'Crm']);
    filter.value = 'stock';
    filter.dispatchEvent(new Event('change', { bubbles: true }));
    expect(cards().map(card => card[0])).toEqual(['Stock levels']);
    click(byText(host, '.ex-card', /^Stock levels/));
    await until(() => expect(location.pathname).toBe('/reports/StockLevels'));
  });

  it('lists the models the person can read, without child tables, and has an honest Dashboards view', async () => {
    const { host } = start(routes(), '/?explorer=data');
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
    const cards = () => [...host.querySelectorAll<HTMLAnchorElement>('.ex-card')].map(card => [text(card.querySelector('b')), text(card.querySelector('.ex-app')), card.getAttribute('href')]);
    expect(cards()).toEqual([['Customers', 'Sales', '/app/customer'], ['Products', 'Stock', '/app/product'], ['Orders', 'Sales', '/app/order'],
      ['Contacts', 'Crm', '/app/contact'], ['Quotes', 'Sales', '/app/quote']]);
    // Child tables (order lines and their parts) are left out.
    expect(host.querySelector('.ex-card[href*="order_lines"]')).toBeNull();
    // No count without the person's counts setting: the Explorer asks for none.
    expect(host.querySelector('.ex-card .count')).toBeNull();
    typeInto(host.querySelector<HTMLInputElement>('.ev-explorer input[type="search"]')!, 'stock');
    expect(cards().map(card => card[0])).toEqual(['Products']);
    expect(text(host.querySelector('.ex-card .ex-desc'))).toBe('Things we sell, with their stock.');

    click(byText(host, '.ev-explorer .seg button', 'Dashboards'));
    await until(() => expect(location.search).toBe('?explorer=dashboards'));
    expect(text(host.querySelector('.ev-explorer'))).toContain('No dashboards yet');
    expect(host.querySelector('.ex-card')).toBeNull();
    // The view is in the address, so Back returns to Data.
    window.history.back();
    await until(() => expect(location.search).toBe('?explorer=data'));
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
    expect(byText(host, '.ev-explorer .seg button', 'Data')!.getAttribute('aria-checked')).toBe('true');
  });

  const openPalette = async () => {
    press(document.body, 'k', { ctrlKey: true });
    await until(() => expect(document.querySelector('.palette input')).not.toBeNull());
    return document.querySelector<HTMLInputElement>('.palette input')!;
  };

  it('is listed in the search palette, with each of its views', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    typeInto(await openPalette(), 'explorer');
    const pages = () => [...document.querySelectorAll('.pal-sec[aria-label="Pages and actions"] .pal-it .lbl')].map(text);
    // No ledger report here, so no Ledger reports view.
    await until(() => expect(pages()).toEqual(['Explorer', 'Explorer: Reports', 'Explorer: Data', 'Explorer: Dashboards']));
    click(byText(document.body, '.pal-it', /^Explorer: Data/));
    await until(() => expect(location.search).toBe('?explorer=data'));
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
  });

  it('opens from the palette with what was typed in its search box', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    typeInto(await openPalette(), 'stock');
    const explorerRows = () => [...document.querySelectorAll('.pal-sec[aria-label="Explorer"] .pal-it .lbl')].map(text);
    await until(() => expect(explorerRows()).toEqual(['Search all reports for “stock”', 'Search all data for “stock”']));
    click(byText(document.body, '.pal-it', /^Search all reports/));
    await until(() => expect(location.search).toBe('?explorer=reports&q=stock'));
    await until(() => expect(host.querySelector<HTMLInputElement>('.ev-explorer input[type="search"]')?.value).toBe('stock'));
    expect([...host.querySelectorAll('.ex-card b')].map(text)).toEqual(['Stock levels']);
    // From the Explorer itself, the next search fills the same box.
    typeInto(await openPalette(), 'quote');
    await until(() => expect(byText(document.body, '.pal-it', /^Search all data/)).not.toBeNull());
    click(byText(document.body, '.pal-it', /^Search all data/));
    await until(() => expect(location.search).toBe('?explorer=data&q=quote'));
    await until(() => expect([...host.querySelectorAll('.ex-card b')].map(text)).toEqual(['Quotes']));
    expect(host.querySelector<HTMLInputElement>('.ev-explorer input[type="search"]')!.value).toBe('quote');
  });

  it('keeps the last view for the Explorer part of the box, and opens with G then E', async () => {
    const { host } = start(routes(), '/?explorer=data');
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
    click(host.querySelector('a[href="/app/quote"]'));
    await until(() => expect(location.pathname).toBe('/app/quote'));
    const button = host.querySelector<HTMLButtonElement>('.top-search .ts-explorer')!;
    expect(button.classList.contains('on')).toBe(false);
    click(button);
    await until(() => expect(location.search).toBe('?explorer=data'));
    click(byText(host, '.ev-explorer .seg button', 'Dashboards'));
    await until(() => expect(location.search).toBe('?explorer=dashboards'));
    click(host.querySelector('a[href="/app/quote"]'));
    await until(() => expect(location.pathname).toBe('/app/quote'));
    press(document.body, 'g');
    press(document.body, 'e');
    await until(() => expect(location.search).toBe('?explorer=dashboards'));
  });
});
