// Declared workspaces (docs/ui.md, Workspaces): the sidebar maps /api/schema's `workspaces` to its sections and
// shortcuts, with board views and filters in the list address, slips presented over the page, custom workspaces
// from the site's overrides, and the active shortcut chosen by the address. Also the Explorer's Ledger reports.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';
import { activeShortcut, listQuery, workspaceNav } from '../app/workspaces';
import type { AppSchema, WorkspaceSchema } from '../model/schema';
import type { ScreenModel } from '../app/meta';

afterEach(stopAll);

const links = { list: (model: string, query: Record<string, string> = {}) => `/app/${model}${Object.keys(query).length ? `?${new URLSearchParams(query)}` : ''}`, report: (name: string) => `/reports/${name}` };

// The recorded invoice schema (a State with list tabs and a board, a Boolean-free model) with two declared
// workspaces and a custom one, as /api/schema sends them.
function declared(): any {
  const schema = copy(invoice.schema);
  const reports = copy(screen.report_schema).body.reports;
  schema.body.reports = [...reports, { ...reports[0], name: 'Balances', label: 'Balances', model: null, source_kind: 'ledger', group: '' }];
  schema.body.slips = [{ hand_in_label: 'Quick customer', label: 'Quick customer', name: 'quick_customer' }];
  const customer = schema.body.models.find((model: any) => model.name === 'customer');
  customer.fields.push({ name: 'vip', type: 'boolean', label: 'VIP', required: false, read_only: false, hidden: false, operators: ['eq', 'isnull'] });
  schema.body.workspaces = [
    { key: 'billing', label: 'Billing', icon: 'receipt', app: 'app', source: 'declared', groups: [
      { key: 'invoices', label: 'Invoices', icon: 'file-text', items: [
        { key: 'model:invoice', kind: 'model', label: 'Invoices', model: 'invoice', view: 'list', filters: null },
        { key: 'model:invoice:board', kind: 'model', label: 'Board', model: 'invoice', view: 'board', filters: null },
        { key: 'model:invoice:issued', kind: 'model', label: 'Issued', model: 'invoice', view: 'list', filters: [['status', 'eq', 'issued']] },
      ] },
      { key: 'people', label: 'Customers', icon: null, items: [
        { key: 'model:customer', kind: 'model', label: 'Customers', model: 'customer', view: 'list', filters: null },
        { key: 'model:customer:vip', kind: 'model', label: 'VIP customers', model: 'customer', view: 'list', filters: [['vip', 'eq', true]] },
        { key: 'slip:quick_customer', kind: 'slip', label: 'Quick customer', slip: 'quick_customer' },
      ] },
    ] },
    { key: 'reports', label: 'Reports', icon: 'chart-column', app: 'app', source: 'declared', groups: [
      { key: 'reports', label: 'Reports', icon: null, items: [
        { key: 'report:CustomerRates', kind: 'report', label: 'Customer rates', report: 'CustomerRates' },
        { key: 'url:docs', kind: 'url', label: 'Docs', url: '/docs' },
        { key: 'url:help', kind: 'url', label: 'Help', url: 'https://example.com/help' },
      ] },
    ] },
    { key: 'desk', label: 'My desk', icon: null, app: null, source: 'custom', groups: [
      { key: 'shortcuts', label: 'Shortcuts', icon: null, items: [{ key: 'model:customer', kind: 'model', label: 'Customers', model: 'customer', view: 'list', filters: null }] },
    ] },
  ] satisfies WorkspaceSchema[];
  return schema;
}
const routes = (schema = declared()): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
  ['GET', /\/api\/customer\?/, screen.customers_page1],
];

describe('workspaceNav from declared workspaces', () => {
  it('maps groups and items to sections and shortcuts, with views, filters, slips and addresses', () => {
    const nav = workspaceNav(declared().body as AppSchema, links);
    expect(nav.workspaces.map(workspace => [workspace.key, workspace.label, workspace.icon, workspace.source])).toEqual([
      ['billing', 'Billing', 'receipt', 'declared'], ['reports', 'Reports', 'chart-column', 'declared'], ['desk', 'My desk', 'layers', 'custom']]);
    const [billing, reports] = nav.workspaces;
    expect(billing.sections.map(section => [section.key, section.label, section.icon])).toEqual([['invoices', 'Invoices', 'file-text'], ['people', 'Customers', 'layers']]);
    expect(billing.sections[0].items.map(item => [item.key, item.href, item.model ?? null])).toEqual([
      ['model:invoice', '/app/invoice', 'invoice'],
      ['model:invoice:board', '/app/invoice?view=board', null],
      // An equality on the list tabs field opens that tab.
      ['model:invoice:issued', '/app/invoice?tab=issued', null],
    ]);
    // A Boolean equality goes in ?f= as the filter rows write it back; a slip has no address.
    expect(billing.sections[1].items.map(item => [item.label, item.href, item.slip ?? null])).toEqual([
      ['Customers', '/app/customer', null], ['VIP customers', `/app/customer?${new URLSearchParams({ f: '[["vip","yes",null]]' })}`, null], ['Quick customer', '', 'quick_customer']]);
    expect(reports.sections[0].items.map(item => [item.href, Boolean(item.external)])).toEqual([['/reports/CustomerRates', false], ['/docs', false], ['https://example.com/help', true]]);
  });

  it('keeps Select equalities and other conditions in the list form', () => {
    const model = { name: 'x', fields: [{ name: 'kind', type: 'select', label: 'Kind' }, { name: 'n', type: 'integer', label: 'N' }] } as unknown as ScreenModel;
    expect(listQuery(model, 'list', [['kind', 'eq', 'a'], ['n', 'gte', 3], { child: 'lines', match: 'any', where: [['qty', 'gt', 1]] }])).toEqual({
      f: JSON.stringify([['kind', 'in', ['a']], ['n', 'gte', 3], { child: 'lines', match: 'any', where: [['qty', 'gt', 1]] }]) });
    expect(listQuery(model, 'board', null)).toEqual({ view: 'board' });
  });

  it('matches the address with its view, tab and filters, the most specific shortcut winning', () => {
    const nav = workspaceNav(declared().body as AppSchema, links);
    const label = (path: string) => activeShortcut(nav, path)?.item.label ?? null;
    expect(label('/app/invoice')).toBe('Invoices');
    expect(label('/app/invoice?view=board&sort=-number')).toBe('Board');
    expect(label('/app/invoice?tab=issued')).toBe('Issued');
    expect(label('/app/invoice?tab=paid')).toBe('Invoices');
    expect(label(`/app/customer?f=${encodeURIComponent('[["vip", "yes", null]]')}`)).toBe('VIP customers');
    expect(label(`/app/customer?f=${encodeURIComponent('[["vip","no",null]]')}`)).toBe('Customers');
    // A record belongs to the plain list's shortcut.
    expect(label('/app/invoice/000000000001a?rtab=details')).toBe('Invoices');
    expect(label('/docs')).toBe('Docs');
    expect(label('/settings')).toBeNull();
  });

  it('matches a list tab shortcut on the list\'s home tab, which the address leaves out', () => {
    const schema = declared().body;
    schema.models.find((model: any) => model.name === 'invoice').list_tabs.default = 'issued';
    const nav = workspaceNav(schema as AppSchema, links);
    expect(activeShortcut(nav, '/app/invoice')?.item.label).toBe('Issued');
    expect(activeShortcut(nav, '/app/invoice?tab=all')?.item.label).toBe('Invoices');
  });

  it('keeps the fallback only for a server without workspaces', () => {
    const schema = declared().body;
    schema.workspaces = [];
    expect(workspaceNav(schema, links).workspaces).toEqual([]);
    delete schema.workspaces;
    expect(workspaceNav(schema, links).workspaces.length).toBeGreaterThan(0);
  });
});

describe('the sidebar with declared workspaces', () => {
  it('shows the groups, highlights the shortcut the address picks out, and lists custom workspaces in the switcher', async () => {
    const { host } = start(routes(), '/app/invoice?tab=issued');
    await until(() => expect(host.querySelector('.nav-sc.active')).not.toBeNull());
    const sections = [...host.querySelectorAll('.nav-sec')].map(group => [text(group.querySelector('.nav-sec-h .lbl')), [...group.querySelectorAll('.nav-sc .lbl')].map(text)]);
    expect(sections).toEqual([['Invoices', ['Invoices', 'Board', 'Issued']], ['Customers', ['Customers', 'VIP customers', 'Quick customer']]]);
    expect(text(host.querySelector('.nav-sc.active .lbl'))).toBe('Issued');
    expect(host.querySelectorAll('.sidebar [aria-current="page"]').length).toBe(1);
    click(byText(host, '.nav-sc', 'Board'));
    await until(() => expect(location.search).toBe('?view=board'));
    await until(() => expect(text(host.querySelector('.nav-sc.active .lbl'))).toBe('Board'));
    // The slip is a button.
    expect(byText(host, '.nav-sc', 'Quick customer')!.tagName).toBe('BUTTON');

    click(host.querySelector('.app-switch'));
    const tiles = [...document.querySelectorAll<HTMLButtonElement>('.ws-pop .ws-tile')];
    expect(tiles.map(text)).toEqual(['Billing', 'Reports', 'My desk']);
    // Without the Customize workspaces access (the route answers 404) there is no Customize button.
    await until(() => expect(document.querySelector('.ws-pop .ws-edit')).toBeNull());
    click(tiles[2]);
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(text(host.querySelector('.app-switch .as-text b'))).toBe('My desk');
  });

  it('presents a menu slip from its shortcut', async () => {
    const { host, sent } = start(routes(), '/app/customer');
    await until(() => expect(byText(host, '.nav-sc', 'Quick customer')).toBeTruthy());
    click(byText(host, '.nav-sc', 'Quick customer'));
    await until(() => expect(requests(sent, 'POST', /\/api\/_slips\/quick_customer/).length).toBeGreaterThan(0));
  });

  it('offers Customize in the switcher to people the workspace routes answer', async () => {
    const { host } = start([...routes(), ['GET', /\/api\/_controls\/workspaces$/, { status: 200, etag: null, body: { items: [], problems: [] } }]], '/app/invoice');
    await until(() => expect(host.querySelector('.app-switch')).not.toBeNull());
    click(host.querySelector('.app-switch'));
    await until(() => expect(text(document.querySelector('.ws-pop .ws-edit'))).toBe('Customize'));
  });
});

describe('the Explorer ledger reports', () => {
  it('shows a Ledger reports view only when a ledger report exists, searchable, in the same cards', async () => {
    const { host } = start(routes(), '/?explorer=ledger');
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
    expect([...host.querySelectorAll('.ex-views button')].map(text)).toEqual(['Reports', 'Ledger reports', 'Data', 'Dashboards']);
    expect([...host.querySelectorAll('.ex-card b')].map(text)).toEqual(['Balances']);
    expect(host.querySelector('.ex-card')!.getAttribute('href')).toBe('/reports/Balances');
    expect((host.querySelector('.ev-explorer .ex-tools') as HTMLElement).hidden).toBe(false);
    stopAll();
    const plain = declared();
    plain.body.reports = plain.body.reports.filter((report: any) => report.source_kind !== 'ledger');
    const again = start(routes(plain), '/?explorer=ledger');
    await until(() => expect(again.host.querySelector('.ex-views')).not.toBeNull());
    expect([...again.host.querySelectorAll('.ex-views button')].map(text)).toEqual(['Reports', 'Data', 'Dashboards']);
    // An old link to the view opens Reports.
    expect(text(again.host.querySelector('.ex-views [aria-pressed="true"], .ex-views [aria-checked="true"], .ex-views .on'))).toBe('Reports');
  });
});
