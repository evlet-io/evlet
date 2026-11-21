// The sidebar's three levels (docs/ui.md#sidebar-and-workspaces): bold section titles that never collapse, parents
// with their icon on a tile in their colour (a parent may be a link), children in the parent's colour on a guide line,
// Home directly under the switcher, new-tab items with the open-externally icon, and bounded badge counts.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';
import { badgeText, workspaceNav } from '../app/workspaces';
import type { AppSchema, WorkspaceSchema } from '../model/schema';
import { Icon } from '../ui/base';

afterEach(stopAll);

const links = { list: (model: string, query: Record<string, string> = {}) => `/app/${model}${Object.keys(query).length ? `?${new URLSearchParams(query)}` : ''}`, report: (name: string) => `/reports/${name}` };
const build = { key: 'build', label: 'Build' }, account = { key: 'account', label: 'Account' };

function schema(): any {
  const recorded = copy(invoice.schema);
  recorded.body.workspaces = [
    { key: 'billing', label: 'Billing', icon: 'receipt', app: 'app', source: 'declared', home: { key: 'home', kind: 'home', label: 'Home', icon: null },
      groups: [
        { key: 'invoicing', label: 'Invoicing', icon: 'file-text', color: 'teal', section: build, link: false, items: [
          { key: 'model:invoice', kind: 'model', label: 'Invoices', model: 'invoice', view: 'list', filters: null, icon: 'receipt', badge: false },
          { key: 'model:invoice:issued', kind: 'model', label: 'Issued', model: 'invoice', view: 'list', filters: [['docstate', 'eq', 'issued']], icon: null, badge: true },
          { key: 'model:invoice:board', kind: 'model', label: 'Board', model: 'invoice', view: 'board', filters: null, icon: null, badge: true },
        ] },
        { key: 'model_customer', label: 'Customers', icon: 'users', color: 'amber', section: build, link: true, items: [
          { key: 'model:customer', kind: 'model', label: 'Customers', model: 'customer', view: 'list', filters: null, icon: 'users', badge: false },
        ] },
        { key: 'url_docs', label: 'Docs', icon: 'file-text', color: 'blue', section: account, link: true, items: [
          { key: 'url:docs', kind: 'url', label: 'Docs', url: 'https://docs.example.com', icon: 'file-text', new_tab: true },
        ] },
        { key: 'more', label: 'More', icon: null, color: null, section: account, link: false, items: [
          { key: 'url:reports', kind: 'url', label: 'All reports', url: '/reports', icon: null, new_tab: true },
          { key: 'url:here', kind: 'url', label: 'Same tab', url: '/reports?x=1', icon: null, new_tab: false },
        ] },
      ] },
  ] satisfies WorkspaceSchema[];
  return recorded;
}

const BADGES = { 'model:invoice:issued': { count: 150, more: true, partial: false }, 'model:invoice:board': { count: 7, more: false, partial: true } };
const routes = (recorded = schema()): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, recorded],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
  ['GET', /\/api\/customer\?/, screen.customers_page1],
  ['GET', /\/api\/_nav\/badges\?workspace=billing$/, { status: 200, etag: null, body: { badges: BADGES } }],
];

describe('the sidebar\'s sections, parents and Home', () => {
  it('maps sections, parent colours, parent links, item icons, new-tab items and Home', () => {
    const [billing] = workspaceNav(schema().body as AppSchema, links).workspaces;
    expect(billing.home).toMatchObject({ key: 'home', label: 'Home', icon: 'house', href: '/' });
    expect(billing.sections.map(entry => [entry.key, entry.title?.label ?? null, entry.tint, entry.link])).toEqual([
      ['invoicing', 'Build', 'teal', false], ['model_customer', 'Build', 'amber', true], ['url_docs', 'Account', 'blue', true], ['more', 'Account', billing.sections[3].tint, false]]);
    // A parent without a colour takes a stable one from its key.
    expect(billing.sections[3].tint).toMatch(/^(blue|green|amber|rose|violet|teal|slate)$/);
    // An item's own icon replaces its kind's; badges and new tabs come from the schema.
    expect(billing.sections[0].items.map(item => [item.icon, item.badge ?? false])).toEqual([['receipt', false], ['list', true], ['square-kanban', true]]);
    expect(billing.sections[3].items.map(item => item.external)).toEqual([true, false]);
  });

  it('reads a badge as nothing for 0, the number found under the cap, and 99+ from it', () => {
    expect(badgeText(undefined)).toBeNull();
    expect(badgeText({ count: 0, more: false, partial: true })).toBeNull();
    expect(badgeText({ count: 7, more: false, partial: true })).toBe('7');
    expect(badgeText({ count: 99, more: false, partial: false })).toBe('99');
    expect(badgeText({ count: 100, more: true, partial: false })).toBe('99+');
  });

  it('draws Home under the switcher, bold section titles, parent tiles in their colour and children on the guide line', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.nav-title')).not.toBeNull());
    const nav = host.querySelector('.sidebar .nav')!;
    // Home is first, above Docs and every section, with no title or parent; the switcher sits above it.
    const home = nav.firstElementChild as HTMLElement;
    expect(home.matches('a.nav-item.nav-home')).toBe(true);
    expect(text(home)).toBe('Home');
    expect(home.getAttribute('href')).toBe('/');
    expect(home.querySelector('.nav-tile')!.innerHTML).toBe(Icon('house', { size: 'sm' }).outerHTML);
    expect(host.querySelector('.sidebar > .side-head .app-switch')).not.toBeNull();
    // Titles: one per section, in order, never buttons.
    expect([...nav.querySelectorAll('.nav-title')].map(text)).toEqual(['Build', 'Account']);
    expect(nav.querySelector('.nav-title button')).toBeNull();
    // Parents: the tile in the parent's colour; a parent shows no count. Children take the parent's colour.
    const invoicing = byText(host, '.nav-sec', /^Invoicing/)!;
    expect(invoicing.querySelector('.nav-sec-h .nav-tile')!.classList.contains('b-teal')).toBe(true);
    expect(invoicing.style.getPropertyValue('--nav-tint')).toBe('var(--t-teal)');
    expect(invoicing.querySelector('.nav-sec-h .count, .nav-sec-h .nav-badge')).toBeNull();
    // A parent link opens its item directly, with no children or chevron.
    const customers = byText(host, '.nav-sec', 'Customers')!;
    expect(customers.querySelector('a.nav-par')!.getAttribute('href')).toBe('/app/customer');
    expect(customers.querySelector('.nav-sec-chev, .nav-sec-items')).toBeNull();
    expect(customers.querySelector('.nav-tile')!.classList.contains('b-amber')).toBe(true);
  });

  it('opens new-tab items in a new tab with the open-externally icon, the others in place', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.nav-title')).not.toBeNull());
    const docs = byText(host, 'a.nav-par', 'Docs')!;
    expect(docs.getAttribute('target')).toBe('_blank');
    expect(docs.getAttribute('rel')).toBe('noopener external');
    expect(docs.lastElementChild!.classList.contains('nav-out')).toBe(true);
    const reports = byText(host, 'a.nav-sc', 'All reports')!;
    expect(reports.getAttribute('target')).toBe('_blank');
    expect(reports.querySelector('.nav-out')).not.toBeNull();
    const here = byText(host, 'a.nav-sc', 'Same tab')!;
    expect(here.getAttribute('target')).toBeNull();
    expect(here.querySelector('.nav-out')).toBeNull();
  });

  it('asks for the badges once the workspace shows, caps them at 99+, and asks again only after a while', async () => {
    const { host, sent } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelectorAll('.nav-badge')).toHaveLength(2));
    expect([...host.querySelectorAll('.nav-sc')].map(row => [text(row.querySelector('.lbl')), text(row.querySelector('.nav-badge')) || null])).toEqual([
      ['Invoices', null], ['Issued', '99+'], ['Board', '7'], ['All reports', null], ['Same tab', null]]);
    expect(byText(host, '.nav-sc', /^Board/)!.querySelector('.nav-badge')!.getAttribute('title')).toBe('Among the latest 1,000');
    expect(requests(sent, 'GET', /\/api\/_nav\/badges/)).toHaveLength(1);
    // Moving to another page within 30 seconds asks nothing more.
    click(byText(host, 'a.nav-sc', /^Issued/));
    await until(() => expect(host.querySelector('.nav-sc.active')).not.toBeNull());
    expect(requests(sent, 'GET', /\/api\/_nav\/badges/)).toHaveLength(1);
  });

  it('asks for no badges when no shortcut declares one, and draws no Home without one', async () => {
    const recorded = schema();
    const billing = recorded.body.workspaces[0];
    billing.home = null;
    for (const group of billing.groups) for (const item of group.items) if ('badge' in item) item.badge = false;
    const { host, sent } = start(routes(recorded), '/app/invoice');
    await until(() => expect(host.querySelector('.nav-title')).not.toBeNull());
    expect(host.querySelector('.nav-home')).toBeNull();
    expect(requests(sent, 'GET', /\/api\/_nav\/badges/)).toHaveLength(0);
  });

  it('styles the light grey sidebar, the white active card with its marker, the titles and the tiles', () => {
    const css = (globalThis as any).process.getBuiltinModule('node:fs').readFileSync(`${(globalThis as any).process.cwd()}/src/evlet/ui/styles.css`, 'utf8') as string;
    expect(css).toContain('.sidebar{background:var(--bg-subtle)}');
    expect(css).toMatch(/\.nav-title\{[^}]*font-weight:700;[^}]*text-transform:uppercase/);
    expect(css).toMatch(/\.nav-tile\{width:24px;height:24px;border-radius:var\(--r\)/);
    expect(css).toMatch(/\.nav-sec-items\{[^}]*border-left:1px solid var\(--border-strong\)/);
    expect(css).toMatch(/\.nav-item\.nav-sc\.active::before\{[^}]*background:var\(--nav-tint/);
    expect(css).toContain('.sidebar .nav-item:hover,.sidebar .nav-sec-h:hover{background:var(--side-hover);color:var(--fg)}');
  });
});
