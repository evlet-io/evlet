// Declared workspaces (docs/ui.md, Workspaces): the sidebar maps /api/schema's `workspaces` to its sections and
// shortcuts, with board views and filters in the list address, slips presented over the page, custom workspaces
// from the site's overrides, and the active shortcut chosen by the address. Also the Explorer's Ledger reports,
// the navigation icons (app/nav-icons.ts) and the top bar: history before the breadcrumb, search and the Explorer in one box.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, press, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';
import { activeShortcut, listQuery, workspaceNav } from '../app/workspaces';
import type { AppSchema, WorkspaceSchema } from '../model/schema';
import type { ScreenModel } from '../app/meta';
import { EXPLORER_ICONS, KIND_ICONS, SHELL_ICONS, kindOf, type ShortcutKind } from '../app/nav-icons';
import { ICONS } from '../ui/icon-data';
import { Icon } from '../ui/base';

afterEach(stopAll);
const node = (globalThis as any).process;

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
/** GET /api/_controls/workspaces for someone the routes answer. */
const listing: Recorded = { status: 200, etag: null, body: { items: [], problems: [] } };
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

  it('matches only the shortcut whose view, tab and filters are exactly the address\'s', () => {
    const nav = workspaceNav(declared().body as AppSchema, links);
    const label = (path: string) => activeShortcut(nav, path)?.item.label ?? null;
    expect(label('/app/invoice')).toBe('Invoices');
    // Search, sort, page and the record panel don't count; the order of the parts doesn't either.
    expect(label('/app/invoice?sort=-number&view=board&q=a&page=2')).toBe('Board');
    expect(label('/app/invoice?tab=issued&open=000000000001a')).toBe('Issued');
    // A tab or filter no shortcut names marks none, not the plain list.
    expect(label('/app/invoice?tab=paid')).toBeNull();
    expect(label('/app/invoice?tab=issued&view=board')).toBeNull();
    expect(label(`/app/customer?f=${encodeURIComponent('[["vip", "yes", null]]')}`)).toBe('VIP customers');
    expect(label(`/app/customer?f=${encodeURIComponent('[["vip","no",null]]')}`)).toBeNull();
    // A record belongs to the plain list's shortcut.
    expect(label('/app/invoice/000000000001a?rtab=details')).toBe('Invoices');
    expect(label('/docs')).toBe('Docs');
    expect(label('/settings')).toBeNull();
  });

  it('opens a plain shortcut on All when the list has a default tab, and never marks a shortcut for that default', () => {
    const schema = declared().body;
    schema.models.find((model: any) => model.name === 'invoice').list_tabs.default = 'issued';
    const nav = workspaceNav(schema as AppSchema, links);
    // A shortcut opens exactly what it names: the plain list and the board say All, the tab shortcut its tab.
    expect(nav.workspaces[0].sections[0].items.map(item => [item.label, item.href, item.model ?? null])).toEqual([
      ['Invoices', '/app/invoice?tab=all', 'invoice'], ['Board', '/app/invoice?view=board&tab=all', null], ['Issued', '/app/invoice?tab=issued', null]]);
    const label = (path: string) => activeShortcut(nav, path)?.item.label ?? null;
    expect(label('/app/invoice?tab=all')).toBe('Invoices');
    expect(label('/app/invoice?tab=issued')).toBe('Issued');
    // Reached without ?tab= (search, a link), the list opens on its default tab and no shortcut is marked.
    expect(label('/app/invoice')).toBeNull();
    // A record still belongs to the plain list's shortcut.
    expect(label('/app/invoice/000000000001a')).toBe('Invoices');
    // The fallback's shortcuts say All too.
    delete schema.workspaces;
    expect(workspaceNav(schema as AppSchema, links).workspaces.flatMap(workspace => workspace.sections.flatMap(section => section.items))
      .find(item => item.model === 'invoice')?.href).toBe('/app/invoice?tab=all');
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
    const tiles = [...document.querySelectorAll<HTMLButtonElement>('.ws-pop .ws-item')];
    expect(tiles.map(text)).toEqual(['Billing', 'Reports', 'My desk']);
    // Without the Customize workspaces access (the route answers 404) there is no Customize button.
    await until(() => expect(document.querySelector('.ws-pop .ws-edit')).toBeNull());
    click(tiles[2]);
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(text(host.querySelector('.app-switch .as-text b'))).toBe('My desk');
  });

  it('marks only the clicked shortcut on a list with a default tab, and none when the list is reached without a tab', async () => {
    const schema = declared();
    schema.body.models.find((model: any) => model.name === 'invoice').list_tabs.default = 'issued';
    const { host } = start(routes(schema), '/app/invoice');
    const tabOf = () => text(host.querySelector('.ev-list [role="tab"][aria-selected="true"]'));
    const marked = () => [...host.querySelectorAll('.sidebar [aria-current="page"]')].map(item => text(item.querySelector('.lbl')));
    // Reached without ?tab=: the default tab, and nothing marked, its section included.
    await until(() => expect(tabOf()).toMatch(/^Issued/));
    expect(location.search).toBe('');
    expect(marked()).toEqual([]);
    expect(host.querySelector('.nav-sec.on')).toBeNull();
    // The plain shortcut opens All, and only it is marked.
    click(byText(host, '.nav-sc', 'Invoices'));
    await until(() => expect(tabOf()).toMatch(/^All invoices/));
    expect(location.search).toBe('?tab=all');
    await until(() => expect(marked()).toEqual(['Invoices']));
    // The tab shortcut opens its tab, the default one, and keeps it in the address, so only it is marked.
    click(byText(host, '.nav-sc', 'Issued'));
    await until(() => expect(tabOf()).toMatch(/^Issued/));
    expect(location.search).toBe('?tab=issued');
    await until(() => expect(marked()).toEqual(['Issued']));
    // Choosing another tab in the list leaves the shortcut behind.
    click(byText(host, '.ev-list [role="tab"]', /^All invoices/));
    await until(() => expect(marked()).toEqual(['Invoices']));
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(location.search).toBe(''));
    expect(marked()).toEqual([]);
  });

  it('keeps the workspace when a page of another one opens, and the breadcrumb\'s workspace crumb switches to it', async () => {
    const { host, router } = start(routes(), '/app/customer');
    const workspace = () => text(host.querySelector('.app-switch .as-text b'));
    const marked = () => [...host.querySelectorAll('.sidebar [aria-current="page"]')].map(item => text(item.querySelector('.lbl')));
    // A fresh tab opens on the page's first declared workspace.
    await until(() => expect(workspace()).toBe('Billing'));
    click(host.querySelector('.app-switch'));
    click([...document.querySelectorAll<HTMLButtonElement>('.ws-pop .ws-tile, .ws-item')].find(item => text(item) === 'My desk')!);
    await until(() => expect(workspace()).toBe('My desk'));
    await until(() => expect(marked()).toEqual(['Customers']));
    // Invoices belong to Billing only: the sidebar stays on My desk with nothing marked.
    void router.navigate('/app/invoice');
    await until(() => expect(text(host.querySelector('.crumbs .cur'))).toBe('Invoices'));
    expect(workspace()).toBe('My desk');
    expect(marked()).toEqual([]);
    // The first crumb names the page's workspace, as a link to the page; clicking it shows that workspace.
    const crumb = host.querySelector<HTMLAnchorElement>('.crumbs a.ws-crumb')!;
    expect(text(crumb)).toBe('Billing');
    expect(crumb.getAttribute('href')).toBe('/app/invoice');
    click(crumb);
    await until(() => expect(workspace()).toBe('Billing'));
    expect(location.pathname).toBe('/app/invoice');
    await until(() => expect(marked()).toEqual(['Invoices']));
  });

  it('keeps the chosen workspace over a reload of the tab, and a fresh tab opens on the page\'s', async () => {
    const first = start(routes(), '/app/customer');
    await until(() => expect(text(first.host.querySelector('.app-switch .as-text b'))).toBe('Billing'));
    click(first.host.querySelector('.app-switch'));
    click([...document.querySelectorAll<HTMLButtonElement>('.ws-pop .ws-tile, .ws-item')].find(item => text(item) === 'My desk')!);
    await until(() => expect(text(first.host.querySelector('.app-switch .as-text b'))).toBe('My desk'));
    void first.router.navigate('/app/invoice');
    await until(() => expect(location.pathname).toBe('/app/invoice'));
    const kept = Object.entries({ ...sessionStorage }).find(([key]) => key.endsWith('.nav.workspace'))!;
    expect(kept[1]).toBe('desk');
    // A reload: the tab's sessionStorage survives, and the workspace with it.
    stopAll();
    sessionStorage.setItem(kept[0], kept[1]);
    const again = start(routes(), '/app/invoice');
    await until(() => expect(text(again.host.querySelector('.crumbs .cur'))).toBe('Invoices'));
    expect(text(again.host.querySelector('.app-switch .as-text b'))).toBe('My desk');
    // A fresh tab (no sessionStorage) opens on the page's own workspace.
    stopAll();
    const fresh = start(routes(), '/app/invoice');
    await until(() => expect(text(fresh.host.querySelector('.crumbs .cur'))).toBe('Invoices'));
    expect(text(fresh.host.querySelector('.app-switch .as-text b'))).toBe('Billing');
  });

  it('opens on the page\'s workspace when sessionStorage is blocked', async () => {
    const real = Object.getOwnPropertyDescriptor(window, 'sessionStorage')!;
    Object.defineProperty(window, 'sessionStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
    try {
      const { host } = start(routes(), '/app/invoice');
      await until(() => expect(text(host.querySelector('.crumbs .cur'))).toBe('Invoices'));
      expect(text(host.querySelector('.app-switch .as-text b'))).toBe('Billing');
      // Switching still works; nothing is remembered.
      click(host.querySelector('.app-switch'));
      click([...document.querySelectorAll<HTMLButtonElement>('.ws-pop .ws-tile, .ws-item')].find(item => text(item) === 'My desk')!);
      await until(() => expect(text(host.querySelector('.app-switch .as-text b'))).toBe('My desk'));
    } finally { Object.defineProperty(window, 'sessionStorage', real); }
  });

  it('presents a menu slip from its shortcut', async () => {
    const { host, sent } = start(routes(), '/app/customer');
    await until(() => expect(byText(host, '.nav-sc', 'Quick customer')).toBeTruthy());
    click(byText(host, '.nav-sc', 'Quick customer'));
    await until(() => expect(requests(sent, 'POST', /\/api\/_slips\/quick_customer/).length).toBeGreaterThan(0));
  });

  it('offers Customize in the switcher to people the workspace routes answer', async () => {
    const { host } = start([...routes(), ['GET', /\/api\/_controls\/workspaces$/, listing]], '/app/invoice');
    await until(() => expect(host.querySelector('.app-switch')).not.toBeNull());
    click(host.querySelector('.app-switch'));
    await until(() => expect(text(document.querySelector('.ws-pop .ws-edit'))).toBe('Customize'));
  });

  // GET /api/_settings/me reports customize_workspaces; the switcher asks the route only when it is missing.
  const me = (customize?: boolean): Recorded => {
    const value = copy(screen.settings_me);
    if (customize === undefined) delete value.body.customize_workspaces;
    else value.body.customize_workspaces = customize;
    return value;
  };
  const openSwitcher = async (settings: Recorded) => {
    const started = start([...routes(), ['GET', /\/api\/_settings\/me$/, settings], ['GET', /\/api\/_controls\/workspaces$/, listing]], '/app/invoice');
    await until(() => expect(started.host.querySelector('.app-switch')).not.toBeNull());
    click(started.host.querySelector('.app-switch'));
    await until(() => expect(document.querySelectorAll('.ws-pop .ws-item').length).toBe(3));
    return started;
  };

  it('shows Customize when settings say the person may customize, without asking the route', async () => {
    const { sent } = await openSwitcher(me(true));
    await until(() => expect(text(document.querySelector('.ws-pop .ws-edit'))).toBe('Customize'));
    expect(requests(sent, 'GET', /\/api\/_controls\/workspaces$/)).toEqual([]);
  });

  it('hides Customize when settings say the person may not, without asking the route', async () => {
    const { sent } = await openSwitcher(me(false));
    // The route would answer here, so a probe would show the button.
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(document.querySelector('.ws-pop .ws-edit')).toBeNull();
    expect(requests(sent, 'GET', /\/api\/_controls\/workspaces$/)).toEqual([]);
  });

  it('asks the route once when settings leave the key out (an older server)', async () => {
    const { sent } = await openSwitcher(me());
    await until(() => expect(text(document.querySelector('.ws-pop .ws-edit'))).toBe('Customize'));
    expect(requests(sent, 'GET', /\/api\/_controls\/workspaces$/).length).toBe(1);
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

describe('the navigation icons', () => {
  it('maps each kind to one icon of the icon set, and the shell and Explorer views too', () => {
    const kinds = Object.keys(KIND_ICONS) as ShortcutKind[];
    expect(kinds).toEqual(['list', 'board', 'report', 'dashboard', 'slip', 'link']);
    // One icon per kind, never shared between kinds.
    expect(new Set(Object.values(KIND_ICONS)).size).toBe(kinds.length);
    for (const name of [...Object.values(KIND_ICONS), ...Object.values(SHELL_ICONS), ...Object.values(EXPLORER_ICONS)]) expect(ICONS[name], name).toBeDefined();
    expect(kindOf({ kind: 'model', view: 'list' })).toBe('list');
    expect(kindOf({ kind: 'model', view: 'board' })).toBe('board');
    expect(kindOf({ kind: 'report' })).toBe('report');
    expect(kindOf({ kind: 'slip' })).toBe('slip');
    expect(kindOf({ kind: 'url' })).toBe('link');
    // The editor's added shortcuts have no kind, only a spec.
    expect(kindOf({ model: 'task', view: 'board' })).toBe('board');
    expect(kindOf({ url: 'https://example.com' })).toBe('link');
  });

  it('gives every shortcut its kind\'s icon, whatever the model, and a filtered list the list icon', () => {
    const nav = workspaceNav(declared().body as AppSchema, links);
    const icons = nav.workspaces.slice(0, 2).map(workspace => workspace.sections.map(section => section.items.map(item => [item.kind, item.icon])));
    expect(icons).toEqual([
      [[['list', 'list'], ['board', 'square-kanban'], ['list', 'list']], [['list', 'list'], ['list', 'list'], ['slip', 'sticky-note']]],
      [[['report', 'chart-column'], ['link', 'link'], ['link', 'link']]],
    ]);
    // Group icons stay as declared.
    expect(nav.workspaces[0].sections.map(section => section.icon)).toEqual(['file-text', 'layers']);
  });

  it('draws the kind\'s icon at the start of each shortcut row, white on the active row, and in the collapsed sidebar with a tooltip', async () => {
    const { host } = start(routes(), '/app/invoice?tab=issued');
    await until(() => expect(host.querySelector('.nav-sc.active')).not.toBeNull());
    const rows = [...host.querySelectorAll<HTMLElement>('.nav-sc')];
    expect(rows.map(row => row.getAttribute('data-kind'))).toEqual(['list', 'board', 'list', 'list', 'list', 'slip']);
    for (const row of rows) {
      // The icon comes first, in place of the dot bullet.
      expect(row.firstElementChild!.classList.contains('nav-ic')).toBe(true);
      expect(row.firstElementChild!.innerHTML).toBe(Icon(KIND_ICONS[row.getAttribute('data-kind') as ShortcutKind]).innerHTML);
      expect(row.querySelector('.nav-dot')).toBeNull();
    }
    const active = host.querySelector('.nav-sc.active')!;
    expect(active.firstElementChild!.classList.contains('nav-ic')).toBe(true);
    // The icon is muted, and white on the active row's solid blue.
    const css = node.getBuiltinModule('node:fs').readFileSync(`${node.cwd()}/src/evlet/ui/styles.css`, 'utf8');
    expect(css).toContain('.nav-sc .nav-ic{color:var(--fg-3)}');
    expect(css).toContain('.nav-sc.active .nav-ic{color:var(--nav-on-fg)}');
    // Collapsed: the same icons, each with its name in a tooltip.
    click(host.querySelector('.side-edge'));
    await until(() => expect(host.querySelector('.app.collapsed')).not.toBeNull());
    const board = byText(host, '.nav-sc', 'Board')!;
    expect(board.querySelector('svg.nav-ic')).not.toBeNull();
    board.dispatchEvent(new MouseEvent('mouseenter'));
    await until(() => expect(text(document.querySelector('.tip'))).toBe('Board'));
  });

  it('shows the kind\'s icon beside each item in the workspace editor, with no icon picker', async () => {
    const editorRoutes: Route[] = [...routes(), ['GET', /\/api\/_controls\/workspaces$/, { status: 200, etag: null, body: { items: [
      { key: 'billing', label: 'Billing', source: 'declared', app: 'app', override: null, changed_by: null, changed_at: null,
        declared: declared().body.workspaces[0], effective: declared().body.workspaces[0] }], problems: [] } }]];
    const { host } = start(editorRoutes, '/app/invoice');
    await until(() => expect(host.querySelector('.app-switch')).not.toBeNull());
    click(host.querySelector('.app-switch'));
    await until(() => expect(document.querySelector('.ws-pop .ws-edit')).not.toBeNull());
    click(document.querySelector('.ws-pop .ws-edit'));
    await until(() => expect(document.querySelectorAll('.wse-item').length).toBe(6));
    const kinds = [...document.querySelectorAll('.wse-item .wse-kind')].map(icon => icon.innerHTML);
    expect(kinds).toEqual(['list', 'board', 'list', 'list', 'list', 'slip'].map(kind => Icon(KIND_ICONS[kind as ShortcutKind]).innerHTML));
    expect(document.querySelector('.wse-modal [aria-label*="icon" i]')).toBeNull();
  });
});

describe('the top bar', () => {
  it('puts the history button at the left end, just before the breadcrumb', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.topbar .crumbs')).not.toBeNull());
    const history = host.querySelector<HTMLElement>('.topbar .rec-btn')!;
    expect(history.parentElement!.classList.contains('topbar')).toBe(true);
    expect(history.nextElementSibling!.classList.contains('crumbs')).toBe(true);
    // After the brand and the Workspace switcher, which follow the phone's menu button, the first thing in the bar.
    expect(history.previousElementSibling!.classList.contains('top-left')).toBe(true);
    expect(history.previousElementSibling!.previousElementSibling!.classList.contains('mobile-only')).toBe(true);
    expect(history.querySelector('svg')!.innerHTML).toBe(Icon(SHELL_ICONS.history).innerHTML);
    expect(history.getAttribute('aria-label')).toBe('Recent records');
    history.dispatchEvent(new MouseEvent('mouseenter'));
    await until(() => expect(text(document.querySelector('.tip'))).toBe('Recent records'));
    // Arrow down still opens its menu.
    press(history, 'ArrowDown');
    await until(() => expect(document.querySelector('.rec-pop')).not.toBeNull());
  });

  it('shows one search button in the top bar and puts the Explorer button at the end of the palette\'s field', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.top-search')).not.toBeNull());
    const box = host.querySelector<HTMLElement>('.top-search')!;
    const buttons = [...box.querySelectorAll<HTMLButtonElement>('button')];
    expect(buttons.map(button => [button.className, button.getAttribute('aria-label')])).toEqual([['ts-search', 'Search']]);
    // No Explorer part, no divider, and no Explorer button anywhere in the top bar.
    expect(box.querySelector('.ts-explorer, .ts-div')).toBeNull();
    expect(host.querySelector('.topbar [aria-label="Explorer"]')).toBeNull();
    buttons[0].dispatchEvent(new MouseEvent('mouseenter'));
    // jsdom is not a Mac: the key reads Ctrl+K (duplicate.test.ts covers both).
    await until(() => expect(text(document.querySelector('.tip'))).toBe('Search Ctrl+K'));
    click(buttons[0]);
    await until(() => expect(document.querySelector('.palette')).not.toBeNull());
    // In the palette: an icon-only Explorer button right after the field, so Tab reaches it from there.
    const field = document.querySelector<HTMLInputElement>('.palette .pal-in input')!;
    const explorer = document.querySelector<HTMLButtonElement>('.palette .pal-in .pal-ex')!;
    expect(field.nextElementSibling).toBe(explorer);
    expect(explorer.tagName).toBe('BUTTON');
    expect(explorer.getAttribute('tabindex')).toBeNull();
    expect(explorer.getAttribute('aria-label')).toBe('Explorer');
    expect(text(explorer)).toBe('');
    expect(explorer.querySelector('svg')!.innerHTML).toBe(Icon(SHELL_ICONS.explorer).innerHTML);
    explorer.dispatchEvent(new MouseEvent('mouseenter'));
    await until(() => expect(text(document.querySelector('.tip'))).toBe('Explorer G E'));
    explorer.dispatchEvent(new MouseEvent('mouseleave'));
    explorer.dispatchEvent(new Event('focus'));
    await until(() => expect(text(document.querySelector('.tip'))).toBe('Explorer G E'));
  });

  it('marks a URL shortcut only on its own address, so the Explorer is never marked twice', async () => {
    const schema = declared();
    schema.body.workspaces[1].groups[0].items.push({ key: 'url:all_reports', kind: 'url', label: 'All reports', url: '/?explorer=reports' });
    const nav = workspaceNav(schema.body as AppSchema, links);
    const label = (path: string) => activeShortcut(nav, path)?.item.label ?? null;
    expect(label('/?explorer=reports')).toBe('All reports');
    expect(label('/?explorer=data')).toBeNull();
    expect(label('/?explorer=reports&q=stock')).toBeNull();
    expect(label('/')).toBeNull();

    const { host } = start(routes(schema), '/?explorer=data');
    await until(() => expect(host.querySelector('.ex-card')).not.toBeNull());
    // The top bar has no Explorer button to mark.
    expect(host.querySelector('.top-search .ts-explorer')).toBeNull();
    expect(host.querySelectorAll('.sidebar .active, .sidebar [aria-current="page"]').length).toBe(0);
  });
});
