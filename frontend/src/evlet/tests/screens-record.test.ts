import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';

afterEach(() => { vi.useRealTimers(); stopAll(); });

const orderRoutes = (patch: Route[2] = recorded.update): Route[] => [
  ...adminRoutes(),
  ['GET', /\/api\/order\/1$/, recorded.detail],
  ['GET', /\/api\/order\/1\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/1$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/1$/, { ...recorded.product, status: 200 }],
  ['PATCH', /\/api\/order\/1$/, patch],
];
const field = (host: Element, name: string) => host.querySelector<HTMLElement>(`.ff[data-field="${name}"]`);
const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('.grid tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === col)!;

describe('generated record view', () => {
  it('lays out the header, summary, Details sections and a tab per Table from the schema', async () => {
    const { host } = start(orderRoutes(), '/ui/m/order/1');
    await until(() => expect(text(host.querySelector('.doc-head .eyebrow'))).toBe('Order'));
    expect(text(host.querySelector('.doc-head h1'))).toContain('Big order');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Big order');
    expect([...host.querySelectorAll('.doc-tabs [role="tab"]')].map(text)).toEqual(['Details', 'Lines120']);
    // Without key fields the summary shows the calculated values.
    await until(() => expect([...host.querySelectorAll('.sum-t span')].map(text)).toEqual(['Due', 'Total', 'Line count']));
    expect(text(host.querySelector('.fs h3'))).toBe('Details');
    expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['title', 'customer', 'stage', 'issued', 'due', 'reason', 'remark', 'total', 'line_count']);
    // reason applies only when the stage is lost.
    expect(field(host, 'reason')!.hidden).toBe(true);
    expect(text(field(host, 'total')!.querySelector('label'))).toContain('ƒ calculated');
    expect(text(host.querySelector('.fs-link'))).toContain('120 rows');
    // The action from the schema is a header button; the record is remembered in the sidebar.
    expect(byText(host, '.doc-head button', 'Close')).not.toBeNull();
    await until(() => expect(text(byText(host, '.nav-group', /^Recent/))).toContain('Big order'));
  });

  it('saves one field in place with the record revision and offers Undo', async () => {
    const { host, sent } = start(orderRoutes(), '/ui/m/order/1');
    await until(() => expect(field(host, 'title')?.querySelector('button.fval')).toBeTruthy());
    click(field(host, 'title')!.querySelector('button.fval'));
    const input = field(host, 'title')!.querySelector('input')!;
    input.value = 'Bigger order';
    press(input, 'Enter');
    await until(() => expect(requests(sent, 'PATCH', /order\/1$/)).toHaveLength(1));
    const patch = requests(sent, 'PATCH', /order\/1$/)[0];
    expect(patch.body.title).toBe('Bigger order');
    expect(patch.headers['if-match']).toBe(recorded.detail.etag);
    await until(() => expect(lastToast()).toContain('Title saved'));
    expect(text(host.querySelector('.crumbs .cur'))).toBe(recorded.update.body.title);
  });

  it('keeps the draft and offers Reload when the record changed elsewhere', async () => {
    const { host } = start(orderRoutes(recorded.stale_if_match), '/ui/m/order/1');
    await until(() => expect(field(host, 'title')?.querySelector('button.fval')).toBeTruthy());
    click(field(host, 'title')!.querySelector('button.fval'));
    const input = field(host, 'title')!.querySelector('input')!;
    input.value = 'Mine';
    press(input, 'Enter');
    await until(() => expect(text(host.querySelector('.inv-banner.b-rose'))).toContain('This record changed since you opened it'));
    expect(byText(host, '.inv-banner button', 'Reload')).not.toBeNull();
    expect((field(host, 'title')!.querySelector('input') as HTMLInputElement).value).toBe('Mine');
  });

  it('edits a line in the Lines tab, then saves the change set from the save bar', async () => {
    const { host, sent } = start(orderRoutes(), '/ui/m/order/1?tab=t-lines');
    await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
    await until(() => expect(text(host.querySelector('.g-t .n'))).toBe('120'));
    const qty = cellOf(rowsIn(host)[1], 'qty');
    click(qty);
    const input = qty.querySelector('input')!;
    input.value = '3';
    press(input, 'Enter');
    await until(() => expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes'));
    press(host.querySelector('.doc')!, 'Enter', { metaKey: true });
    await until(() => expect(requests(sent, 'PATCH', /order\/1$/)).toHaveLength(1));
    const body = requests(sent, 'PATCH', /order\/1$/)[0].body;
    expect(body.children.lines.update).toEqual([{ id: recorded.detail.body.children.lines[1].id, qty: '3.0000' }]);
    await until(() => expect(host.querySelector('.ev-savebar')).toBeNull());
    expect(location.search).toBe('?tab=t-lines');
  });

  it('asks before leaving with unsaved lines, and stays when asked to keep editing', async () => {
    const { host, router } = start(orderRoutes(), '/ui/m/order/1?tab=t-lines');
    await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
    const qty = cellOf(rowsIn(host)[0], 'qty');
    click(qty);
    const input = qty.querySelector('input')!;
    input.value = '7';
    press(input, 'Enter');
    press(document.activeElement!, 'Escape');
    const leaving = router.go('docs', {});
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Discard unsaved changes?'));
    click(byText(document.body, '.modal-f button', 'Cancel'));
    expect(await leaving).toBe(false);
    expect(location.pathname).toBe('/ui/m/order/1');
    const again = router.go('docs', {});
    await until(() => expect(document.querySelector('.modal')).not.toBeNull());
    click(byText(document.body, '.modal-f button', 'Discard changes'));
    expect(await again).toBe(true);
    await until(() => expect(location.pathname).toBe('/ui/docs'));
  });

  it('runs an action with its scalar arguments through the action dialog', async () => {
    const { host, sent } = start([...orderRoutes(), ['POST', /\/api\/order\/1\/actions\/close$/, recorded.action]], '/ui/m/order/1');
    await until(() => expect(byText(host, '.doc-head button', 'Close')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Close'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe('Close');
    typeInto(dialog.querySelector('input')!, 'Signed off');
    click(byText(dialog, '.modal-f button', 'Close'));
    await until(() => expect(requests(sent, 'POST', /actions\/close$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /actions\/close$/)[0].body).toEqual({ note: 'Signed off' });
    await until(() => expect(lastToast()).toContain('Close done'));
  });

  it('creates a record from a form with initial values from the address, then opens it', async () => {
    const created = { status: 201, etag: '"new"', body: { id: 56, name: 'Acme Trading', rate: '0.00', credit_days: 30 } };
    const { host, sent } = start([...adminRoutes(),
      ['POST', /\/api\/customer$/, created],
      ['GET', /\/api\/customer\/56$/, { ...created, status: 200 }],
    ], '/ui/m/customer/new?name=Acme%20Trading');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    const name = host.querySelector<HTMLInputElement>('.fld[data-field="name"] input')!;
    expect(name.value).toBe('Acme Trading');
    expect(host.querySelector('.doc-side')).toBeNull();
    typeInto(host.querySelector<HTMLInputElement>('.fld[data-field="credit_days"] input')!, '30');
    click(byText(host, '.ev-savebar button', 'Create customer'));
    await until(() => expect(location.pathname).toBe('/ui/m/customer/56'));
    expect(requests(sent, 'POST', /customer$/)[0].body).toMatchObject({ name: 'Acme Trading', credit_days: 30 });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toContain('Acme Trading'));
    expect(lastToast()).toContain('Customer created');
  });
});

describe('permission-driven screens', () => {
  const viewer = (): Route[] => [
    ['GET', /\/api\/auth\/session$/, screen.viewer_session],
    ['GET', /\/api\/schema$/, screen.viewer_schema],
    ['GET', /\/api\/contact\?/, screen.viewer_contacts],
    ['GET', /\/api\/contact\/1$/, screen.viewer_contact_detail],
    ['GET', /\/api\/contact\/1\/attachments$/, screen.attachments],
  ];

  it('a viewer sees only readable fields, no New, no actions, and nothing editable', async () => {
    const { host } = start(viewer(), '/ui/m/contact');
    await until(() => expect([...host.querySelectorAll('thead th')].map(text)).toEqual(['Name', 'Email']));
    expect(byText(host, '.ph-actions button', /New contact/)).toBeNull();
    expect(host.querySelector('.topbar .btn.primary')).toBeNull();
    // No delete and no State: rows are not selectable.
    expect(host.querySelector('tbody [role="checkbox"]')).toBeNull();
    click(host.querySelector('a.ev-row-link'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Mariam Haddad'));
    expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['name', 'email']);
    expect(host.querySelector('.fs button.fval')).toBeNull();
    expect(byText(host, '.doc-head button', 'Mark contacted')).toBeNull();
    expect(text(byText(host, '.side-sec', /^Files/))).toContain('No files yet.');
    expect(host.querySelector('.ev-upload')).toBeNull();
  });

  it('the admin of the same records gets every field, the action and an upload; a refused upload says why', async () => {
    const { host, sent } = start([...adminRoutes(),
      ['GET', /\/api\/contact\/1$/, screen.contact_detail],
      ['GET', /\/api\/contact\/1\/attachments$/, screen.attachments],
      ['POST', /\/api\/contact\/1\/attachments$/, screen.attachment_upload],
    ], '/ui/m/contact/1');
    await until(() => expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['name', 'email', 'phone', 'notes']));
    expect(host.querySelectorAll('.fs button.fval').length).toBe(4);
    expect(byText(host, '.doc-head button', 'Mark contacted')).not.toBeNull();
    const upload = host.querySelector<HTMLInputElement>('.ev-upload input')!;
    Object.defineProperty(upload, 'files', { value: [new File(['Hello'], 'brief.txt', { type: 'text/plain' })] });
    upload.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(requests(sent, 'POST', /attachments$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /attachments$/)[0].body).toEqual({ name: 'brief.txt', content: 'SGVsbG8=' });
    await until(() => expect(lastToast()).toBe('Not attached: Method not allowed'));
  });
});
