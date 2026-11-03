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
  it('shows the list fields with the title pinned first, the total and pages through the server', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    expect([...host.querySelectorAll('thead th')].map(text)).toEqual(['', 'Name', 'Rate', 'Credit days']);
    expect(host.querySelector('thead th[data-key="name"]')!.classList.contains('stk')).toBe(true);
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
    ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ] as any[];
  const draftId = invoice.invoice_draft.body.id, issuedNumber = invoice.invoice_revise_before.body.number;

  it('shows saved views per state with counts, and the declared measures', async () => {
    const { host, sent } = start(routes(), '/app/invoice');
    await until(() => expect([...host.querySelectorAll('.ev-list [role="tab"]')].map(text)).toEqual(['All invoices3', 'Draft1', 'Issued1', 'Paid0', 'Void1']));
    await until(() => expect([...host.querySelectorAll('.sum-t')].map(item => text(item.querySelector('span')) + '=' + text(item.querySelector('b')))).toEqual(['Outstanding=AED 2,680.00', 'Count=3']));
    expect(query(requests(sent, 'GET', /aggregate/)[0])).toMatchObject({ measures: 'n:count,outstanding', group_by: 'status' });
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(JSON.parse(listCalls(sent, 'invoice').pop()!.filters)).toEqual({ status: 'issued' }));
    expect(JSON.parse(query(requests(sent, 'GET', /invoice\/measures/).pop()!).filters)).toEqual({ status: 'issued' });
    // The state shows as a pill in the list.
    expect(text(host.querySelector('tbody .pill'))).not.toBe('');
  });

  it('moves a card with the keyboard through the move route, and explains a blocked move', async () => {
    const { host, sent } = start([...routes(),
      ['GET', new RegExp(`/api/invoice/${draftId}$`), invoice.invoice_draft],
      ['POST', new RegExp(`/api/invoice/${draftId}/moves/issued$`), invoice.invoice_issued],
    ], '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    const column = (label: string) => byText(host, '.col-k', new RegExp(`^${label}`))!;
    expect([...host.querySelectorAll('.col-h .pill')].map(text)).toEqual(['Draft', 'Issued', 'Paid', 'Void']);
    const draftCard = column('Draft').querySelector<HTMLElement>('.card')!;
    expect(text(draftCard.querySelector('.t'))).toBe('Draft invoice');
    press(draftCard, 'ArrowRight', { altKey: true });
    await until(() => expect(column('Issued').querySelectorAll('.card').length).toBe(2));
    await until(() => expect(requests(sent, 'POST', /moves\/issued$/)).toHaveLength(1));
    const move = requests(sent, 'POST', /moves\/issued$/)[0];
    expect(move.headers['if-match']).toBe(invoice.invoice_draft.etag);
    // The issued card moves on to Paid only for admins: this editor is told why, and nothing is sent.
    const issued = byText(column('Issued'), '.card', new RegExp(issuedNumber))!;
    press(issued, 'ArrowRight', { altKey: true });
    expect(lastToast()).toContain('Your role cannot use Mark paid');
    expect(requests(sent, 'POST', /moves\/paid/)).toHaveLength(0);
  });

  it('puts a card back when the server refuses the move', async () => {
    const { host } = start([...routes(),
      ['GET', new RegExp(`/api/invoice/${draftId}$`), invoice.invoice_draft_blocked],
      ['POST', new RegExp(`/api/invoice/${draftId}/moves/issued$`), invoice.invoice_move_refused],
    ], '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    const draft = byText(host, '.col-k', /^Draft/)!;
    press(draft.querySelector('.card')!, 'ArrowRight', { altKey: true });
    await until(() => expect([...document.querySelectorAll('.toast')].map(text).join(' ')).toContain('Issue is not available: the condition for this move is not met'));
    await until(() => expect(draft.querySelectorAll('.card').length).toBe(1));
  });
});
