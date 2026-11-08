// Duplicate a saved record (docs/ui.md#duplicate): the More menu item and Alt+Shift+D open a new,
// unsaved record of the same model with the saved record's values, on its page or in the side panel.
import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, invoice, press, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';

function platform(name: 'MacIntel' | 'Win32') { Object.defineProperty(navigator, 'platform', { value: name, configurable: true }); }
afterEach(() => { stopAll(); delete (navigator as any).platform; });

const dialog = () => document.querySelector<HTMLElement>('.modal');
const dialogTitle = () => text(dialog()?.querySelector('h2'));
const menuItems = () => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
const altShiftD = (target: Element | Document, init: KeyboardEventInit = {}) => press(target, 'Î', { code: 'KeyD', altKey: true, shiftKey: true, ...init });
const empty = { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } };

/**
 * The saved order, with Remark marked unique and Issued closed to this role. `change` adjusts the order
 * model in the schema. Its 120 lines (one with two parts) come in two pages.
 */
function orderRoutes(change: (model: any) => void = () => {}): Route[] {
  const schema = copy(screen.schema);
  const order = schema.body.models.find((model: any) => model.name === 'order');
  for (const field of order.fields) {
    if (field.name === 'remark') field.unique = true;
    if (field.name === 'issued') field.read_only = true;
  }
  change(order);
  const detail = copy(recorded.detail);
  Object.assign(detail.body, { remark: 'Rush' });
  const created = copy(recorded.detail);
  Object.assign(created.body, { id: '000000000000z', title: 'Big order' });
  created.status = 201;
  return [
    ['GET', /\/api\/auth\/session$/, recorded.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', /\/api\/order\/0000000000001$/, detail],
    ['GET', /\/api\/order\/\w+\/lines\/lines\?/, recorded.lines_page],
    ['GET', /\/api\/order\/000000000000z$/, { ...created, status: 200 }],
    ['POST', /\/api\/order$/, created],
    ['PATCH', /\/api\/order\/\w+$/, recorded.update],
    ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
    ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
    ['GET', /\/api\/(order|customer|product)\?/, empty],
  ];
}

async function openOrder(routes: Route[] = orderRoutes()) {
  const started = start(routes, '/app/order/0000000000001');
  await until(() => expect(text(started.host.querySelector('.doc-head h1'))).toBe('Big order'));
  return started;
}

const openMore = (host: Element) => click(host.querySelector('.doc-head button[aria-label="More actions"]'));
const duplicateItem = () => menuItems().find(element => text(element.querySelector('span')) === 'Duplicate') ?? null;

describe('Duplicate in the More menu', () => {
  for (const mac of [true, false]) {
    it(`shows Duplicate with its key hint (${mac ? '⌥⇧D' : 'Alt+Shift+D'}) where the role may create`, async () => {
      platform(mac ? 'MacIntel' : 'Win32');
      const { host } = await openOrder();
      openMore(host);
      const item = menuItems().find(element => text(element.querySelector('span')) === 'Duplicate');
      expect(item).toBeDefined();
      expect(text(item!.querySelector('kbd'))).toBe(mac ? '⌥⇧D' : 'Alt+Shift+D');
    });
  }

  it('leaves Duplicate out where the role cannot create, and on a read-only model', async () => {
    for (const change of [(model: any) => { model.permissions.create = false; }, (model: any) => { model.api = 'read'; }]) {
      const { host } = await openOrder(orderRoutes(change));
      openMore(host);
      expect(menuItems().map(element => text(element.querySelector('span')))).not.toContain('Duplicate');
      // Nor does the key do anything.
      expect(altShiftD(document.body).defaultPrevented).toBe(false);
      expect(location.pathname).toBe('/app/order/0000000000001');
      stopAll();
    }
  });
});

describe('what Duplicate copies', () => {
  it('opens a new order with the plain fields and every line and part, dirty from the start; Create sends a POST without ids', async () => {
    const { host, sent } = await openOrder();
    await until(() => expect(text(host.querySelector('.doc-tabs, [role="tablist"]'))).toContain('120'));
    openMore(host);
    click(duplicateItem());
    await until(() => expect(location.pathname).toBe('/app/order/new'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    // Nothing went through the address.
    expect(location.search).toBe('');
    expect(text(host.querySelector('.inv-banner'))).toContain('Copied from Big order.');
    // Remark is unique: left empty and named in the note. Issued is closed to this role: not copied, not named.
    expect(text(host.querySelector('.ev-dup-unique'))).toBe('Unique fields were left empty: Remark.');
    expect(host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!.value).toBe('Big order');
    expect(text(host.querySelector('[role="tablist"]'))).toContain('120');
    // Leaving asks: the copied values are unsaved.
    click(byText(host, '.crumbs a, a', 'Orders'));
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(dialog()!, 'button', 'Cancel'));
    expect(location.pathname).toBe('/app/order/new');
    click(byText(host, '.ev-savebar button', 'Create order'));
    await until(() => expect(requests(sent, 'POST', /\/api\/order$/)).toHaveLength(1));
    expect(requests(sent, 'PATCH', /\/api\/order/)).toHaveLength(0);
    const body = requests(sent, 'POST', /\/api\/order$/)[0].body as any;
    expect(body).toMatchObject({ title: 'Big order', customer: '0000000000001', stage: 'open' });
    // Not the id, the name, calculated fields, the unique Remark or the unwritable Issued.
    for (const field of ['id', 'name', 'due', 'total', 'line_count', 'remark', 'issued']) expect(body).not.toHaveProperty(field);
    expect(body.children.lines).toHaveLength(120);
    const first = body.children.lines[0];
    expect(first).toMatchObject({ product: '0000000000001', qty: '1.0000', rate: '2.50' });
    expect(first).not.toHaveProperty('id');
    expect(first).not.toHaveProperty('amount');
    expect(first).not.toHaveProperty('order');
    expect(first.children.parts).toEqual([expect.objectContaining({ hours: '1.5000' }), expect.objectContaining({ hours: '2.0000' })]);
    expect(first.children.parts.some((part: any) => 'id' in part)).toBe(false);
    await until(() => expect(location.pathname).toBe('/app/order/000000000000z'));
  });

  it('leaves out the number, the State, calculated and read-only fields; the copy starts in the default state', async () => {
    const id = String(invoice.invoice_draft.body.id);
    const created = copy(invoice.invoice_draft);
    Object.assign(created.body, { id: '000000000000y' });
    created.status = 201;
    const routes: Route[] = [
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, invoice.schema],
      ['GET', /\/api\/invoice\/\w+\/(measures|journal|attachments)/, { status: 200, etag: null, body: { items: [], users: {}, total: 0, next: null } }],
      ['GET', /\/api\/invoice\/\w+\/related\//, empty],
      ['GET', new RegExp(`/api/invoice/${id}$`), { ...invoice.invoice_draft, body: { ...invoice.invoice_draft.body, number: 'INV-0007', remarks: 'Call first' } }],
      ['GET', /\/api\/invoice\/000000000000y$/, { ...created, status: 200 }],
      ['POST', /\/api\/invoice$/, created],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
      ['GET', /\/api\/\w+\?/, empty],
    ];
    const { host, sent } = start(routes, `/app/invoice/${id}`);
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    altShiftD(document.body);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New invoice'));
    expect(host.querySelector('.ev-dup-unique')).toBeNull();
    click(byText(host, '.ev-savebar button', 'Create invoice'));
    await until(() => expect(requests(sent, 'POST', /\/api\/invoice$/)).toHaveLength(1));
    const body = requests(sent, 'POST', /\/api\/invoice$/)[0].body as any;
    expect(body).toMatchObject({ customer: invoice.invoice_draft.body.customer, posting_date: '2026-11-01', due_on: '2026-12-01', remarks: 'Call first' });
    for (const field of ['id', 'number', 'status', 'total', 'revises', 'name', 'title']) expect(body).not.toHaveProperty(field);
    expect(body.children.lines).toEqual([
      expect.objectContaining({ item: 'Support hours', qty: '10.0000', rate: '250.00' }),
      expect.objectContaining({ item: 'Licence', qty: '2.0000', rate: '90.00' }),
    ]);
    expect(body.children.lines.some((line: any) => 'id' in line || 'amount' in line)).toBe(false);
  });
});

describe('what the server can turn off (duplicate: false, copy: false)', () => {
  it('a model with duplicate: false offers no Duplicate, in the menu or by key', async () => {
    const { host } = await openOrder(orderRoutes(model => { model.duplicate = false; }));
    openMore(host);
    expect(duplicateItem()).toBeNull();
    expect(altShiftD(document.body).defaultPrevented).toBe(false);
    expect(location.pathname).toBe('/app/order/0000000000001');
  });

  it('leaves fields and tables marked copy: false as a new record starts them', async () => {
    const routes = orderRoutes(model => {
      model.fields.find((field: any) => field.name === 'title').copy = false;
      model.children.find((child: any) => child.name === 'lines').copy = false;
    });
    const { host, sent } = await openOrder(routes);
    altShiftD(document.body);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    // Title starts at its default, as on any new order, not at the source's value.
    expect(host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!.value).toBe('order');
    expect(text(host.querySelector('[role="tablist"]'))).not.toContain('120');
    // The rest is still copied; Create sends no title and no lines.
    host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!.value = 'Second order';
    host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!.dispatchEvent(new Event('change', { bubbles: true }));
    click(byText(host, '.ev-savebar button', 'Create order'));
    await until(() => expect(requests(sent, 'POST', /\/api\/order$/)).toHaveLength(1));
    const body = requests(sent, 'POST', /\/api\/order$/)[0].body as any;
    expect(body).toMatchObject({ title: 'Second order', customer: '0000000000001', stage: 'open' });
    expect(body.children?.lines ?? []).toHaveLength(0);
  });

  it('leaves a nested table marked copy: false empty while its parent rows are copied', async () => {
    const schema = (routes: Route[]) => routes.find(route => String(route[1]) === String(/\/api\/schema$/))![2] as any;
    const routes = orderRoutes();
    schema(routes).body.models.find((model: any) => model.name === 'order_lines').children.find((child: any) => child.name === 'parts').copy = false;
    const { host, sent } = await openOrder(routes);
    altShiftD(document.body);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    click(byText(host, '.ev-savebar button', 'Create order'));
    await until(() => expect(requests(sent, 'POST', /\/api\/order$/)).toHaveLength(1));
    const lines = (requests(sent, 'POST', /\/api\/order$/)[0].body as any).children.lines;
    expect(lines).toHaveLength(120);
    expect(lines[0].children?.parts ?? []).toHaveLength(0);
  });
});

describe('cancelling a duplicate', () => {
  it('asks first, then leaves without keeping the copy as an unfinished draft', async () => {
    const storage = new Map<string, string>();
    const local = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => void storage.set(key, value),
      removeItem: (key: string) => void storage.delete(key), key: (index: number) => [...storage.keys()][index] ?? null, get length() { return storage.size; }, clear: () => storage.clear() } as Storage;
    const { host } = start(orderRoutes(), '/app/order/0000000000001', { storage: local });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Big order'));
    altShiftD(document.body);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    click(byText(host, '.ev-savebar button', 'Cancel'));
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(dialog()!, 'button', 'Discard changes'));
    await until(() => expect(location.pathname).toBe('/app/order'));
    expect([...storage.entries()].some(([key, value]) => key.startsWith('evlet.unsaved.') && value.includes('"order"'))).toBe(false);
  });
});

describe('the Duplicate shortcut', () => {
  it('Alt+Shift+D on a saved record opens the copy once; a held key does nothing', async () => {
    const { host } = await openOrder();
    expect(altShiftD(document.body, { repeat: true }).defaultPrevented).toBe(true);
    expect(location.pathname).toBe('/app/order/0000000000001');
    altShiftD(document.body);
    await until(() => expect(location.pathname).toBe('/app/order/new'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    // On the new record the key does nothing.
    expect(altShiftD(document.body).defaultPrevented).toBe(false);
    expect(location.pathname).toBe('/app/order/new');
  });

  it('is listed on the help sheet; the saved record has no changes and leaves without asking', async () => {
    platform('Win32');
    const { host, router } = await openOrder();
    // A saved record opens without changes, so leaving it never asks (no phantom changes).
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(host.querySelector('.ev-savebar')).toBeNull();
    press(document.body, '?', { shiftKey: true });
    expect([...dialog()!.querySelectorAll('.ev-keys-g')][0].textContent).toContain('Duplicate this order');
    press(document.activeElement ?? document.body, 'Escape');
    expect(dialog()).toBeNull();
    await router.go('list', { model: 'order' });
    expect(dialog()).toBeNull();
    expect(location.pathname).toBe('/app/order');
  });
});

describe('Duplicate in the side panel', () => {
  it('opens the copy in the panel, from the More menu', async () => {
    const id = String(invoice.invoice_draft.body.id);
    const body = copy(invoice.schema).body;
    Object.assign(body.models.find((item: { name: string }) => item.name === 'invoice'), { open_in: 'panel' });
    const routes: Route[] = [
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, { ...invoice.schema, body }],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
      ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
      ['GET', /\/api\/invoice\/\w+\/related\/payment\.invoice/, invoice.invoice_related_payments],
      ['GET', /\/api\/invoice\/\w+\/related\/invoice\.revises/, invoice.invoice_related_revisions],
      ['GET', /\/api\/invoice\/\w+\/journal/, invoice.invoice_journal],
      ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
      ['GET', /\/api\/invoice\/\w{13}$/, invoice.invoice_draft],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
    ];
    const { host } = start(routes, '/app/invoice');
    const panel = () => host.querySelector<HTMLElement>('.ev-panel');
    await until(() => expect(host.querySelector(`.ev-list tbody tr[data-key="${id}"]`)).not.toBeNull());
    click(host.querySelector(`.ev-list tbody tr[data-key="${id}"]`));
    await until(() => expect(text(panel()?.querySelector('.doc-head h1'))).toBe('Draft invoice'));
    click(panel()!.querySelector('button[aria-label="More actions"]'));
    click(duplicateItem());
    await until(() => expect(location.search).toBe('?open=new'));
    await until(() => expect(text(panel()?.querySelector('.doc-head h1'))).toBe('New invoice'));
    expect(text(panel()!.querySelector('.inv-banner'))).toContain('Copied from Draft invoice.');
    // Open full page takes the copy along, lines and banner included.
    click(panel()!.querySelector('.ev-panel-bar button[aria-label="Open full page"]'));
    await until(() => expect(location.pathname).toBe('/app/invoice/new'));
    await until(() => expect(text(host.querySelector('.inv-banner'))).toContain('Copied from Draft invoice.'));
    expect(text(host.querySelector('[role="tablist"]'))).toContain('2');
  });
});

describe('the top bar search key', () => {
  for (const mac of [true, false]) {
    it(`shows ${mac ? '⌘K' : 'Ctrl+K'} on ${mac ? 'a Mac' : 'Windows'}`, async () => {
      platform(mac ? 'MacIntel' : 'Win32');
      const { host } = start(adminRoutes(), '/docs');
      await until(() => expect(host.querySelector('.ts-search kbd')).not.toBeNull());
      expect(text(host.querySelector('.ts-search kbd'))).toBe(mac ? '⌘K' : 'Ctrl+K');
      host.querySelector('.ts-search')!.dispatchEvent(new Event('focus'));
      expect(text(document.querySelector('.tip'))).toBe(mac ? 'Search ⌘K' : 'Search Ctrl+K');
    });
  }
});
