// Regressions from the review of the generated screens: sign-out, what preferences keep, bulk
// changes against what the list showed, board totals from the server, dialog actions, create
// links, a board with one State tab and empty values.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, invoice, lastToast, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, id, type Sent } from './server';

afterEach(() => { vi.useRealTimers(); stopAll(); try { localStorage.clear(); } catch { /* no storage */ } });

const query = (sent: Sent) => Object.fromEntries(new URL(sent.url, 'http://x').searchParams);
const idOf = (sent: Sent) => sent.url.split('/').pop()!;
const signOut = (host: HTMLElement) => {
  click(host.querySelector('.me'));
  click(byText(document.body, '.pop [role="menuitem"]', 'Sign out'));
};
const orderRecord = (): Route[] => [
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
];

describe('signing out', () => {
  it('keeps the user signed in when the server does not confirm, and signs out on retry', async () => {
    let fail = true;
    const { host, sent } = start([...adminRoutes(),
      ['POST', /\/api\/auth\/logout$/, () => fail ? { status: 500, etag: null, body: { error: 'Internal server error' } } : recorded.logout],
    ]);
    await until(() => expect(host.querySelector('.me')).not.toBeNull());
    signOut(host);
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('You are still signed in'));
    expect(text(document.querySelector('.modal'))).toContain('Internal server error');
    expect(host.querySelector('.ev-login')).toBeNull();
    expect(host.querySelector('.app')).not.toBeNull();

    fail = false;
    click(byText(document.body, '.modal button', 'Try again'));
    await until(() => expect(host.querySelector('.ev-login')).not.toBeNull());
    expect(requests(sent, 'POST', /logout$/)).toHaveLength(2);
  });

  it('treats a session the server already ended as signed out', async () => {
    const { host } = start([...adminRoutes(), ['POST', /\/api\/auth\/logout$/, recorded.unauthenticated]]);
    await until(() => expect(host.querySelector('.me')).not.toBeNull());
    signOut(host);
    await until(() => expect(host.querySelector('.ev-login')).not.toBeNull());
  });
});

describe('preferences hold no record data', () => {
  it('keeps recent records and searches in memory only, drops them on sign-out and keeps the theme', async () => {
    // Left by an earlier build that stored titles and searches.
    localStorage.setItem('evlet.admin.recents', JSON.stringify([{ model: 'order', id: 9, title: 'Old secret' }]));
    localStorage.setItem('evlet.admin.recentQ', JSON.stringify(['old search']));
    const { host } = start([...adminRoutes(), ...orderRecord(), ['POST', /\/api\/auth\/logout$/, recorded.logout]], '/app/order/0000000000001', { storage: localStorage });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toContain('Big order'));
    await until(() => expect(text(byText(host, '.nav-group', /^Recent/))).toContain('Big order'));
    expect(text(host.querySelector('.side'))).not.toContain('Old secret');
    document.documentElement.dataset.theme = 'dark';

    const stored = () => Object.fromEntries(Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)!).map(key => [key, localStorage.getItem(key)]));
    expect(JSON.stringify(stored())).not.toContain('Big order');
    expect(Object.keys(stored())).not.toContain('evlet.admin.recents');
    expect(Object.keys(stored())).not.toContain('evlet.admin.recentQ');

    signOut(host);
    await until(() => expect(host.querySelector('.ev-login')).not.toBeNull());
    // The sign-in screen keeps this device's theme; the theme holds no record data.
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(JSON.stringify(stored())).not.toContain('Big order');
  });
});

describe('bulk changes', () => {
  const page = () => screen.customers_page1.body.items as Array<Record<string, unknown>>;
  const rowsShown = (host: HTMLElement) => host.querySelectorAll('tbody tr[data-key]').length;
  const pick = (host: HTMLElement, ids: number[]) => ids.forEach(n => click(host.querySelector(`tbody tr[data-key="${id(n)}"] [role="checkbox"]`)));

  it('leaves records that changed since the list loaded as they are, and says so', async () => {
    const { host, sent } = start([...adminRoutes(),
      ['GET', /\/api\/customer\?/, screen.customers_page1],
      ['GET', /\/api\/customer\/[0-9a-z]{13}$/, item => {
        const row = { ...page().find(entry => entry.id === idOf(item))! };
        if (row.id === id(2)) row.rate = '99.00';
        return { status: 200, etag: `"rev-${row.id}"`, body: row };
      }],
      ['DELETE', /\/api\/customer\/[0-9a-z]{13}$/, { status: 204, etag: null, body: null }],
    ], '/app/customer');
    await until(() => expect(rowsShown(host)).toBeGreaterThan(0));
    pick(host, [1, 2, 3]);
    click(byText(host.querySelector('.bulk')!, 'button', 'Delete'));
    click(byText(document.body, '.modal-f button', 'Delete'));
    await until(() => expect(lastToast()).toContain('Deleted 2 customers'));
    expect(requests(sent, 'DELETE', /customer/).map(idOf).sort()).toEqual([id(1), id(3)]);
    expect(lastToast()).toContain('1 customer changed since you loaded the list, so it was left as it is.');
  });

  it('runs a few at a time with progress, and stops when the page is left', async () => {
    let open = 0, most = 0;
    const waiting: Array<() => void> = [];
    const { host, sent, router } = start([...adminRoutes(),
      ['GET', /\/api\/customer\?/, screen.customers_page1],
      ['GET', /\/api\/customer\/[0-9a-z]{13}$/, async item => {
        open++; most = Math.max(most, open);
        await new Promise<void>(resolve => waiting.push(resolve));
        open--;
        return { status: 200, etag: '"r"', body: page().find(entry => entry.id === idOf(item))! };
      }],
      ['DELETE', /\/api\/customer\/[0-9a-z]{13}$/, { status: 204, etag: null, body: null }],
    ], '/app/customer');
    await until(() => expect(rowsShown(host)).toBeGreaterThan(0));
    pick(host, [1, 2, 3, 4, 5, 6, 7, 8]);
    click(byText(host.querySelector('.bulk')!, 'button', 'Delete'));
    click(byText(document.body, '.modal-f button', 'Delete'));
    await until(() => expect(waiting).toHaveLength(4));
    expect(text(host.querySelector('.ev-progress'))).toBe('Deleting 0 of 8…');
    waiting.splice(0, 2).forEach(release => release());
    await until(() => expect(text(host.querySelector('.ev-progress'))).toBe('Deleting 2 of 8…'));
    expect(most).toBe(4);

    await router.go('docs', {});
    waiting.splice(0).forEach(release => release());
    await new Promise(resolve => setTimeout(resolve, 50));
    waiting.splice(0).forEach(release => release());
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(requests(sent, 'GET', /customer\/[0-9a-z]{13}$/).length).toBeLessThanOrEqual(6);
    expect(requests(sent, 'DELETE', /customer/).length).toBeLessThanOrEqual(2);
  });

  it('moves only records whose shown values are unchanged', async () => {
    const draftId = invoice.invoice_draft.body.id;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, invoice.schema],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
      ['GET', new RegExp(`/api/invoice/${draftId}$`), () => { const changed = copy(invoice.invoice_draft); changed.body.total = '1.00'; return changed; }],
      ['POST', /moves\/issued$/, invoice.invoice_issued],
    ] as Route[], '/app/invoice');
    await until(() => expect(rowsShown(host)).toBe(3));
    click(host.querySelector(`tbody tr[data-key="${draftId}"] [role="checkbox"]`));
    click(byText(host.querySelector('.bulk')!, 'button', 'Move'));
    click(byText(document.body, '.modal .ev-choices button', 'Issued'));
    await until(() => expect(lastToast()).toContain('1 invoice changed since you loaded the list, so it was left as it is.'));
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
  });
});

describe('board totals and counts from the server', () => {
  const routes = (aggregate: Route[2], schema = invoice.schema): Route[] => [
    ['GET', /\/api\/auth\/session$/, invoice.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', /\/api\/invoice\?/, invoice.invoices],
    ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
    ['GET', /\/api\/invoice\/aggregate/, aggregate],
    ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
    ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ];
  const grouped = { status: 200, etag: null, body: { group_by: ['status'], measures: ['n', 'outstanding'], items: [
    { status: 'draft', n: 5, outstanding: '0.00' }, { status: 'issued', n: 2, outstanding: '5360.10' }, { status: 'void', n: 1, outstanding: '0.00' }] } };

  it('shows each column’s count and the declared measure from the aggregate route', async () => {
    const { host, sent } = start(routes(grouped), '/app/invoice?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    const head = (label: string) => byText(host, '.col-k', new RegExp(`^${label}`))!.querySelector('.col-h')!;
    await until(() => expect(text(head('Draft').querySelector('.ct'))).toBe('5'));
    expect(text(head('Issued').querySelector('.amt'))).toBe('AED 5,360.10');
    // The measure counts only issued invoices, so the draft column has none of it.
    expect(text(head('Draft').querySelector('.amt'))).toBe('AED 0.00');
    expect(text(head('Paid').querySelector('.ct'))).toBe('0');
    const call = query(requests(sent, 'GET', /aggregate/).pop()!);
    expect(call).toMatchObject({ measures: 'n:count,outstanding', group_by: 'status' });
  });

  it('counts the tabs with the list tabs route when the model declares no measures', async () => {
    const schema = copy(invoice.schema);
    delete schema.body.models.find((model: any) => model.name === 'invoice').measures;
    const { host, sent } = start(routes(invoice.invoice_aggregate_by_status, schema), '/app/invoice');
    await until(() => expect([...host.querySelectorAll('.ev-list [role="tab"]')].map(text)).toEqual(['All invoices3', 'Draft1', 'Issued1', 'Paid0', 'Void1']));
    expect(requests(sent, 'GET', /list_tabs/)).not.toHaveLength(0);
  });

  it('shows only the chosen column when a State tab is selected on the board', async () => {
    const { host } = start(routes(grouped), '/app/invoice?view=board&tab=issued');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(1));
    expect([...host.querySelectorAll('.col-h .pill')].map(text)).toEqual(['Issued']);
    expect(text(host.querySelector('.ev-board-note'))).toBe('Showing issued invoices only. Choose All invoices to see every column.');
  });
});

describe('record view', () => {
  it('hides dialog actions, which this screen cannot open', async () => {
    const schema = copy(screen.schema);
    schema.body.models.find((model: any) => model.name === 'order').actions.push({ name: 'reschedule', label: 'Reschedule', dialog: true });
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ...orderRecord()], '/app/order/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Close')).not.toBeNull());
    expect(byText(host, '.doc-head button', 'Reschedule')).toBeNull();
  });

  it('takes create link values only for fields the role can see and write', async () => {
    const schema = copy(screen.schema);
    const order = schema.body.models.find((model: any) => model.name === 'order');
    order.fields.find((field: any) => field.name === 'remark').hidden = true;
    const { host, sent } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
      ['POST', /\/api\/order$/, item => ({ status: 201, etag: '"x"', body: { ...item.body, id: 77 } })],
      ['GET', /\/api\/order\/000000000002d$/, { status: 200, etag: '"x"', body: { id: 77, title: 'T' } }],
    ] as Route[], '/app/order/new?title=T&remark=planted&due=2030-01-01&total=5');
    const create = () => byText(host, '.ev-savebar button', /^Create/);
    await until(() => expect(create()).not.toBeNull());
    click(create());
    await until(() => expect(requests(sent, 'POST', /\/api\/order$/)).toHaveLength(1));
    const body = requests(sent, 'POST', /\/api\/order$/)[0].body;
    expect(body.title).toBe('T');
    expect(body).not.toHaveProperty('remark');
    expect(body).not.toHaveProperty('due');
    expect(body).not.toHaveProperty('total');
  });

  it('shows an empty summary value as Not set', async () => {
    const schema = copy(screen.schema);
    schema.body.models.find((model: any) => model.name === 'order').key_fields = ['title', 'remark'];
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ...orderRecord()], '/app/order/0000000000001');
    await until(() => expect([...host.querySelectorAll('.sum-t > span')].map(text)).toContain('Remark'));
    const due = byText(host, '.sum-t', /^Remark/)!;
    expect(text(due.querySelector('b'))).toBe('Not set');
    expect(due.querySelector('b .ev-unset')).not.toBeNull();
    expect(text(host.querySelector('.ev-record'))).not.toContain('—');
  });
});
