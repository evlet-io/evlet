import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, invoice, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until } from './screens-helpers';
import { copy, id, type Sent } from './server';

afterEach(() => { vi.useRealTimers(); stopAll(); });

const query = (sent: Sent) => Object.fromEntries(new URL(sent.url, 'http://x').searchParams);
const listCalls = (sent: Sent[], model: string) => requests(sent, 'GET', new RegExp(`/api/${model}\\?`)).map(query);
const rowTitles = (host: HTMLElement) => [...host.querySelectorAll('tbody tr[data-key] .ev-row-link')].map(text);

function customers(sent: Sent) {
  const params = query(sent);
  if (params.q === 'medical') return screen.customers_search;
  if (params.order_by === '-rate') return screen.customers_by_rate_desc;
  return params.offset === '50' ? screen.customers_page2 : screen.customers_page1;
}

describe('generated list page', () => {
  it('offers no New or Delete for a read-only model (Meta.api = "read"), whatever the permissions say', async () => {
    const schema = copy(screen.schema);
    const customer = schema.body.models.find((model: { name: string }) => model.name === 'customer');
    Object.assign(customer, { api: 'read', permissions: { create: true, update: true, delete: true } });
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ['GET', /\/api\/customer\?/, customers]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    expect(byText(host, 'button', /New customer/)).toBeFalsy();
    expect(host.querySelector('thead [role="checkbox"]')).toBeNull();
  });

  it('shows the list fields with the title pinned first, the total and pages through the server', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    expect([...host.querySelectorAll('thead th')].map(text)).toEqual(['', 'Customer name', 'Rate', 'Credit days']);
    expect(host.querySelector('thead th[data-key="title"]')!.classList.contains('stk')).toBe(true);
    expect(rowTitles(host)[0]).toBe('Al Sahra Trading');
    expect(host.querySelector<HTMLAnchorElement>(`tbody tr[data-key="${id(1)}"] a.ev-row-link`)!.getAttribute('href')).toBe('/app/customer/0000000000001');
    expect(text(host.querySelector('.ev-pager span'))).toBe('1–50 of 55');
    expect(listCalls(sent, 'customer')[0]).toMatchObject({ limit: '50', offset: '0' });

    click(host.querySelector('.ev-pager [aria-label="Next page"]'));
    await until(() => expect(text(host.querySelector('.ev-pager span'))).toBe('51–55 of 55'));
    expect(listCalls(sent, 'customer').pop()).toMatchObject({ limit: '50', offset: '50' });
    await until(() => expect(location.search).toBe('?page=2'));
    await until(() => expect(rowTitles(host)).toHaveLength(5));
  });

  it('sorts on the server by clicking a header and keeps the order in the address', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers]], '/app/customer');
    await until(() => expect(rowTitles(host).length).toBeGreaterThan(0));
    const rate = host.querySelector<HTMLElement>('thead th[data-key="rate"]')!;
    click(rate);
    await until(() => expect(listCalls(sent, 'customer').pop()).toMatchObject({ order_by: 'rate' }));
    click(rate);
    await until(() => expect(rowTitles(host)[0]).toBe('Trading house 55'));
    expect(listCalls(sent, 'customer').pop()).toMatchObject({ order_by: '-rate' });
    expect(rate.getAttribute('aria-sort')).toBe('descending');
    await until(() => expect(location.search).toBe('?sort=-rate'));
  });

  it('searches as you type, after a short pause, with / to focus the search', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers]], '/app/customer');
    await until(() => expect(rowTitles(host).length).toBeGreaterThan(0));
    press(document.body, '/');
    const input = host.querySelector<HTMLInputElement>('.toolbar input')!;
    expect(document.activeElement).toBe(input);
    typeInto(input, 'medical');
    await until(() => expect(rowTitles(host)).toEqual(['Zayed Medical Supplies', 'Bayan Medical']));
    expect(listCalls(sent, 'customer').filter(call => call.q)).toEqual([expect.objectContaining({ q: 'medical', offset: '0' })]);
    expect(text(host.querySelector('.ev-list .summary'))).toBe('2 customers');
    await until(() => expect(location.search).toBe('?q=medical'));
  });

  it('filters by a choice field with a chip that clears it', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/order\?/, sent => query(sent).filters ? screen.orders_open : recorded.list]], '/app/order');
    await until(() => expect(rowTitles(host)).toEqual(['Big order']));
    click(byText(host, '.ph-actions button', /^Filter/));
    // The builder adds a first row and opens its field search.
    await until(() => expect(document.querySelector('.field-pop input')).not.toBeNull());
    typeInto(document.querySelector<HTMLInputElement>('.field-pop input')!, 'stage');
    press(document.querySelector<HTMLInputElement>('.field-pop input')!, 'Enter');
    await until(() => expect(host.querySelector('.fb-value .chip')).not.toBeNull());
    click(byText(host, '.fb-value .chip', 'Open'));
    await until(() => expect(rowTitles(host)).toEqual(['Warehouse racks', 'Office fit-out']));
    expect(JSON.parse(listCalls(sent, 'order').pop()!.filters)).toEqual([['stage', 'in', ['open']]]);
    expect(text(host.querySelector('.filter-chip'))).toBe('Stage in Open');
    // Stage is a plain choice on this model; the pill appears only for a State.
    expect(host.querySelector('tbody .pill')).toBeNull();
    click(host.querySelector('.filter-chip button'));
    await until(() => expect(rowTitles(host)).toEqual(['Big order']));
    expect(listCalls(sent, 'order').pop()!.filters).toBeUndefined();
  });

  it('a link to a filtered list opens it as it was, including links made before the filter builder', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/order\?/, screen.orders_open]], '/app/order?f=%7B%22stage%22%3A%22open%22%7D');
    await until(() => expect(text(host.querySelector('.filter-chip'))).toBe('Stage in Open'));
    expect(JSON.parse(listCalls(sent, 'order')[0].filters)).toEqual([['stage', 'in', ['open']]]);
    await until(() => expect(new URLSearchParams(location.search).get('f')).toBe('[["stage","in",["open"]]]'));
  });

  it('selects rows and deletes them through the bulk bar, each with its current revision', async () => {
    let deleted: string[] = [];
    const { host, sent } = start([...adminRoutes(),
      ['GET', /\/api\/customer\?/, customers],
      // The current detail still shows what the list showed, so each record is deleted.
      ['GET', /\/api\/customer\/[0-9a-z]{13}$/, sent => ({ status: 200, etag: `"rev-${sent.url.split('/').pop()}"`,
        body: screen.customers_page1.body.items.find((row: { id: string }) => row.id === sent.url.split('/').pop()) })],
      ['DELETE', /\/api\/customer\/[0-9a-z]{13}$/, sent => { deleted.push(sent.url.split('/').pop()!); return { status: 204, etag: null, body: null }; }],
    ], '/app/customer');
    await until(() => expect(rowTitles(host).length).toBeGreaterThan(0));
    click(host.querySelector(`tbody tr[data-key="${id(1)}"] [role="checkbox"]`));
    click(host.querySelector(`tbody tr[data-key="${id(2)}"] [role="checkbox"]`));
    const bulk = host.querySelector('.bulk')!;
    expect(bulk.classList.contains('show')).toBe(true);
    expect(text(bulk.querySelector('.n'))).toBe('2 selected');
    click(byText(bulk, 'button', 'Delete'));
    expect(text(document.querySelector('.modal h2'))).toBe('Delete 2 customers?');
    click(byText(document.body, '.modal-f button', 'Delete'));
    await until(() => expect(deleted.sort()).toEqual([id(1), id(2)]));
    expect(requests(sent, 'DELETE', /customer/).map(item => item.headers['if-match']).sort()).toEqual([`"rev-${id(1)}"`, `"rev-${id(2)}"`]);
    await until(() => expect(document.querySelector('.toast')?.textContent).toContain('Deleted 2 customers'));
  });
});

describe('board and saved views (recorded State fixtures)', () => {
  const routes = () => [
    ['GET', /\/api\/auth\/session$/, invoice.session],
    ['GET', /\/api\/schema$/, invoice.schema],
    ['GET', /\/api\/invoice\?/, invoice.invoices],
    ['GET', /\/api\/invoice\/measures/, (sent: Sent) => copy(new URL(sent.url, 'http://x').searchParams.get('filters') ? invoice.invoice_measures_issued : invoice.invoice_measures)],
    ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
    ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
    ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ] as any[];
  const draftId = invoice.invoice_draft.body.id, issuedNumber = invoice.invoice_locked.body.number;

  it('shows saved views per state with counts, and the declared measures', async () => {
    const { host, sent } = start(routes(), '/app/invoice');
    await until(() => expect([...host.querySelectorAll('.ev-list [role="tab"]')].map(text)).toEqual(['All invoices3', 'Draft1', 'Issued1', 'Paid0', 'Void1']));
    await until(() => expect([...host.querySelectorAll('.sum-t')].map(item => text(item.querySelector('span')) + '=' + text(item.querySelector('b')))).toEqual(['Outstanding=AED 2,680.00', 'Count=3']));
    // Each measure is its own card: an icon by function, the accents in turn, no trend line (none is declared).
    expect([...host.querySelectorAll('.sum > .stat-card')].map(card => card.className)).toEqual(['sum-t stat-card tone-blue has-ic', 'sum-t stat-card tone-teal has-ic']);
    expect([...host.querySelectorAll('.stat-card .stat-ic')]).toHaveLength(2);
    expect(host.querySelector('.stat-card .ev-spark')).toBeNull();
    // The counts come from the list tabs route; the board's aggregate is not needed for the list.
    expect(requests(sent, 'GET', /list_tabs/)).not.toHaveLength(0);
    expect(requests(sent, 'GET', /aggregate/)).toHaveLength(0);
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(JSON.parse(listCalls(sent, 'invoice').pop()!.filters)).toEqual({ status: 'issued' }));
    expect(JSON.parse(query(requests(sent, 'GET', /invoice\/measures/).pop()!).filters)).toEqual({ status: 'issued' });
    // The state shows as a pill in the list.
    expect(text(host.querySelector('tbody .pill'))).not.toBe('');
  });

  it('offers the board only when the model names its State field in Meta.board', async () => {
    const { host } = start(routes(), '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    expect([...host.querySelectorAll('.ph .seg button')].map(text)).toEqual(['List', 'Board']);
  });

  it('opens a State model without Meta.board as a table, with no board switch, even from an old board link', async () => {
    const schema = copy(invoice.schema);
    for (const model of (schema.body as any).models) delete model.board;
    const { host } = start([['GET', /\/api\/schema$/, schema], ...routes().slice(0, 1), ...routes().slice(2)], '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.ev-list tbody tr').length).toBeGreaterThan(0));
    expect(host.querySelector('.board')).toBeNull();
    expect(byText(host, '.seg button', 'Board')).toBeFalsy();
    // The saved views per state stay; only the board is opt-in.
    expect(host.querySelectorAll('.ev-list [role="tab"]').length).toBe(5);
    await until(() => expect(window.location.search).not.toContain('view=board'));
  });

  it('moves a card with the keyboard through the move route, and explains a blocked move', async () => {
    const { host, sent } = start([...routes(),
      ['GET', new RegExp(`/api/invoice/${draftId}$`), invoice.invoice_draft],
      ['POST', new RegExp(`/api/invoice/${draftId}/moves/issue$`), invoice.invoice_issued],
    ], '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    const column = (label: string) => byText(host, '.col-k', new RegExp(`^${label}`))!;
    expect([...host.querySelectorAll('.col-h .pill')].map(text)).toEqual(['Draft', 'Issued', 'Paid', 'Void']);
    const draftCard = column('Draft').querySelector<HTMLElement>('.card')!;
    expect(text(draftCard.querySelector('.t'))).toBe('Draft invoice');
    press(draftCard, 'ArrowRight', { altKey: true });
    await until(() => expect(column('Issued').querySelectorAll('.card').length).toBe(2));
    await until(() => expect(requests(sent, 'POST', /moves\/issue$/)).toHaveLength(1));
    const move = requests(sent, 'POST', /moves\/issue$/)[0];
    expect(move.headers['if-match']).toBe(invoice.invoice_draft.etag);
    // The issued card moves on to Paid only for admins: this editor is told why, and nothing is sent.
    const issued = byText(column('Issued'), '.card', new RegExp(issuedNumber))!;
    press(issued, 'ArrowRight', { altKey: true });
    expect(lastToast()).toContain('Your role cannot use Mark paid');
    expect(requests(sent, 'POST', /moves\/mark_paid/)).toHaveLength(0);
  });

  it('puts a card back when the server refuses the move', async () => {
    const { host } = start([...routes(),
      ['GET', new RegExp(`/api/invoice/${draftId}$`), invoice.invoice_draft_blocked],
      ['POST', new RegExp(`/api/invoice/${draftId}/moves/issue$`), invoice.invoice_move_refused],
    ], '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    const draft = byText(host, '.col-k', /^Draft/)!;
    press(draft.querySelector('.card')!, 'ArrowRight', { altKey: true });
    await until(() => expect([...document.querySelectorAll('.toast')].map(text).join(' ')).toContain('Issue is not available: the condition for this move is not met'));
    await until(() => expect(draft.querySelectorAll('.card').length).toBe(1));
  });
});

describe('list tabs by a chosen field, opening on the model default (Meta.list_tabs, Meta.list_tab)', () => {
  const customerId = invoice.invoice_customer.body.id;
  const withTabs = (tabs: Record<string, string>) => {
    const schema = copy(invoice.schema);
    schema.body.models.find((model: { name: string }) => model.name === 'invoice').list_tabs = tabs;
    return schema;
  };
  const routes = (schema: unknown, extra: any[] = []) => [
    ['GET', /\/api\/auth\/session$/, invoice.session],
    ['GET', /\/api\/schema$/, schema],
    ...extra,
    ['GET', /\/api\/invoice\?/, (sent: Sent) => query(sent).q ? { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } } : copy(invoice.invoices)],
    ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
    ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
    ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
    ['GET', new RegExp(`/api/customer/${customerId}$`), invoice.invoice_customer],
  ] as any[];
  const tabNames = (host: HTMLElement) => [...host.querySelectorAll('.ev-list [role="tab"]')].map(text);
  const selected = (host: HTMLElement) => text(host.querySelector('.ev-list [role="tab"][aria-selected="true"]'));
  const filtersOf = (call: Record<string, string>) => call.filters === undefined ? undefined : JSON.parse(call.filters);

  it('opens on the default tab with nothing in the address, and puts All there when chosen', async () => {
    const { host, sent } = start(routes(withTabs({ field: 'status', kind: 'state', default: 'issued' })), '/app/invoice');
    await until(() => expect(selected(host)).toBe('Issued1'));
    expect(listCalls(sent, 'invoice').map(filtersOf)).toEqual([{ status: 'issued' }]);
    expect(location.search).toBe('');
    click(byText(host, '.ev-list [role="tab"]', /^All invoices/));
    await until(() => expect(location.search).toBe('?tab=all'));
    expect(filtersOf(listCalls(sent, 'invoice').pop()!)).toBeUndefined();
    // Back on the default tab, the address drops it again.
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(location.search).toBe(''));
  });

  it('keeps a tab the address names on arrival, the default one too, until another is chosen', async () => {
    const { host } = start(routes(withTabs({ field: 'status', kind: 'state', default: 'issued' })), '/app/invoice?tab=issued');
    await until(() => expect(selected(host)).toBe('Issued1'));
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(location.search).toBe('?tab=issued');
    click(byText(host, '.ev-list [role="tab"]', /^All invoices/));
    await until(() => expect(location.search).toBe('?tab=all'));
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(location.search).toBe(''));
  });

  it('keeps the tab from the address over the default, and Clear filters goes back to the default', async () => {
    const { host, sent } = start(routes(withTabs({ field: 'status', kind: 'state', default: 'issued' })), '/app/invoice?tab=all&q=zzz');
    await until(() => expect(byText(host, 'button', 'Clear filters')).toBeTruthy());
    expect(selected(host)).toMatch(/^All invoices/);
    expect(listCalls(sent, 'invoice')[0]).toMatchObject({ q: 'zzz' });
    expect(filtersOf(listCalls(sent, 'invoice')[0])).toBeUndefined();
    click(byText(host, 'button', 'Clear filters'));
    await until(() => expect(filtersOf(listCalls(sent, 'invoice').pop()!)).toEqual({ status: 'issued' }));
    await until(() => expect(location.search).toBe(''));
    expect(selected(host)).toMatch(/^Issued/);
  });

  it('offers All when the list is empty only because of its tab', async () => {
    const empty = { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } };
    const { host, sent } = start(routes(withTabs({ field: 'status', kind: 'state', default: 'paid' }),
      [['GET', /\/api\/invoice\?.*filters=/, empty]]), '/app/invoice');
    await until(() => expect(byText(host, 'button', 'Show all invoices')).toBeTruthy());
    expect(text(host.querySelector('.dt-empty h3, .dt-empty .empty-t, .dt-empty'))).toContain('No invoices in this tab');
    click(byText(host, 'button', 'Show all invoices'));
    await until(() => expect(location.search).toBe('?tab=all'));
    expect(filtersOf(listCalls(sent, 'invoice').pop()!)).toBeUndefined();
  });

  it('shows no tabs without list_tabs, even for a model with a State', async () => {
    const schema = copy(invoice.schema);
    delete schema.body.models.find((model: { name: string }) => model.name === 'invoice').list_tabs;
    const { host, sent } = start(routes(schema), '/app/invoice?tab=issued');
    await until(() => expect(rowTitles(host).length + host.querySelectorAll('tbody tr[data-key]').length).toBeGreaterThan(0));
    expect(tabNames(host)).toEqual([]);
    expect(filtersOf(listCalls(sent, 'invoice')[0])).toBeUndefined();
    expect(requests(sent, 'GET', /list_tabs/)).toHaveLength(0);
  });

  it('makes tabs of a Link field’s most used values, labelled by title, and finds the default by its title', async () => {
    const tabs = { status: 200, etag: null, body: { field: 'customer', total: 3, items: [{ value: customerId, count: 3 }], more: false } };
    const found = { status: 200, etag: null, body: { items: [invoice.invoice_customer.body], total: 1, limit: 1, offset: 0 } };
    const { host, sent } = start(routes(withTabs({ field: 'customer', kind: 'link', default: 'Al Sahra Trading' }), [
      ['GET', /\/api\/invoice\/list_tabs/, tabs],
      ['GET', /\/api\/customer\?/, found],
    ]), '/app/invoice');
    await until(() => expect(tabNames(host)).toEqual(['All invoices3', 'Al Sahra Trading3']));
    // The default is looked up by the title first, so the list loads once, on that customer.
    expect(JSON.parse(query(requests(sent, 'GET', /\/api\/customer\?/)[0]).filters)).toEqual({ title: 'Al Sahra Trading' });
    expect(listCalls(sent, 'invoice').map(filtersOf)).toEqual([{ customer: customerId }]);
    expect(selected(host)).toBe('Al Sahra Trading3');
    expect(location.search).toBe('');
    click(byText(host, '.ev-list [role="tab"]', /^All invoices/));
    await until(() => expect(location.search).toBe('?tab=all'));
  });

  it('opens on All when a Link default names no record this person can read', async () => {
    const empty = { status: 200, etag: null, body: { items: [], total: 0, limit: 1, offset: 0 } };
    const { host, sent } = start(routes(withTabs({ field: 'customer', kind: 'link', default: 'Nobody' }), [
      ['GET', /\/api\/invoice\/list_tabs/, { status: 200, etag: null, body: { field: 'customer', total: 3, items: [{ value: customerId, count: 3 }], more: false } }],
      ['GET', /\/api\/customer\?/, empty],
    ]), '/app/invoice');
    await until(() => expect(selected(host)).toMatch(/^All invoices/));
    expect(listCalls(sent, 'invoice').map(filtersOf)).toEqual([undefined]);
    expect(location.search).toBe('');
  });
});
