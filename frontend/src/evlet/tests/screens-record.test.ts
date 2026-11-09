import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { fingerprint } from '../model/finite';
import { copy, id } from './server';

afterEach(() => { vi.useRealTimers(); stopAll(); });

const orderRoutes = (patch: Route[2] = recorded.update): Route[] => [
  ...adminRoutes(),
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
  ['PATCH', /\/api\/order\/0000000000001$/, patch],
];
const field = (host: Element, name: string) => host.querySelector<HTMLElement>(`.ff[data-field="${name}"]`);
// A saved record's title is edited in the header, not in the Details form.
const heading = (host: Element) => host.querySelector<HTMLElement>('.doc-head .doc-title');
const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('.grid tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === col)!;

describe('generated record view', () => {
  const customerRoutes = (orders: number, quotes = 0): Route[] => {
    const page = (total: number) => ({ status: 200, etag: null, body: { items: [], total, limit: 10, offset: 0 } });
    return [...adminRoutes(), ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
      ['GET', /\/api\/customer\/0000000000001\/related\/order\.customer/, page(orders)], ['GET', /\/api\/customer\/0000000000001\/related\/quote\.customer/, page(quotes)]];
  };

  it('shows an empty related list as one line, without a table of headers or a 0', async () => {
    const { host } = start(customerRoutes(0), '/app/customer/0000000000001');
    await until(() => expect(byText(host, '.doc-tabs [role="tab"]', 'Related')).not.toBeNull());
    click(byText(host, '.doc-tabs [role="tab"]', 'Related'));
    await until(() => expect(text(host.querySelector('.ev-related[aria-label="Orders"] .ev-related-empty'))).toBe('No orders yet.'));
    expect(host.querySelector('.ev-related[aria-label="Orders"] table')).toBeNull();
    expect(text(host.querySelector('.ev-related[aria-label="Orders"] .panel-h .num'))).toBe('');
  });

  it('counts a related list named after its model in that model\'s words', async () => {
    for (const [total, expected] of [[1, 'Orders, 1 order'], [3, 'Orders, 3 orders']] as const) {
      const { host } = start(customerRoutes(total), '/app/customer/0000000000001');
      await until(() => expect(host.querySelector('.rel-row')?.getAttribute('aria-label')).toBe(expected));
      stopAll();
    }
  });
  it('lays out the header, summary, Details sections and a tab per Table from the schema', async () => {
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(text(host.querySelector('.doc-head .eyebrow'))).toBe('Order'));
    expect(text(host.querySelector('.doc-head h1'))).toContain('Big order');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Big order');
    expect([...host.querySelectorAll('.doc-tabs [role="tab"]')].map(text)).toEqual(['Details', 'Lines120']);
    // Without key fields the summary shows the calculated values.
    await until(() => expect([...host.querySelectorAll('.sum-t span')].map(text)).toEqual(['Due', 'Total', 'Line count']));
    expect(text(host.querySelector('.fs h3'))).toBe('Details');
    expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['customer', 'stage', 'issued', 'due', 'reason', 'remark', 'total', 'line_count']);
    // reason applies only when the stage is lost.
    expect(field(host, 'reason')!.hidden).toBe(true);
    expect(text(field(host, 'total')!.querySelector('label'))).toContain('ƒ calculated');
    expect(text(host.querySelector('.fs-link'))).toContain('120 rows');
    // The action from the schema is a header button; the record is remembered under the history button
    // on the right of the top bar, left of notifications, not in the sidebar.
    expect(byText(host, '.doc-head button', 'Close')).not.toBeNull();
    expect(byText(host, '.nav-group', /^Recent/)).toBeNull();
    const history = host.querySelector<HTMLElement>('.topbar > .top-actions > .rec-btn')!;
    expect(history).not.toBeNull();
    // The record page's small breadcrumb sits in its main column, directly above the record's header.
    const crumbs = host.querySelector<HTMLElement>('.doc-main > .page-crumbs')!;
    expect(crumbs.nextElementSibling!.classList.contains('doc-head')).toBe(true);
    expect(host.querySelector('.topbar .crumbs')).toBeNull();
    click(history);
    const recent = document.querySelector('.rec-pop .rec-item');
    expect(text(recent)).toContain('Big order');
    expect(text(recent)).toContain('just now');
  });

  it('saves one field in place with the record revision and offers Undo', async () => {
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(heading(host)?.querySelector('button.fval')).toBeTruthy());
    click(heading(host)!.querySelector('button.fval'));
    const input = heading(host)!.querySelector('input')!;
    input.value = 'Bigger order';
    press(input, 'Enter');
    await until(() => expect(requests(sent, 'PATCH', /order\/0000000000001$/)).toHaveLength(1));
    const patch = requests(sent, 'PATCH', /order\/0000000000001$/)[0];
    expect(patch.body.title).toBe('Bigger order');
    expect(patch.headers['if-match']).toBe(recorded.detail.etag);
    await until(() => expect(lastToast()).toContain('Title saved'));
    expect(text(host.querySelector('.crumbs .cur'))).toBe(recorded.update.body.title);
  });

  it('keeps the draft and offers Reload when the record changed elsewhere', async () => {
    const { host } = start(orderRoutes(recorded.stale_if_match), '/app/order/0000000000001');
    await until(() => expect(heading(host)?.querySelector('button.fval')).toBeTruthy());
    click(heading(host)!.querySelector('button.fval'));
    const input = heading(host)!.querySelector('input')!;
    input.value = 'Mine';
    press(input, 'Enter');
    await until(() => expect(text(host.querySelector('.inv-banner.b-rose'))).toContain('This record changed since you opened it'));
    expect(byText(host, '.inv-banner button', 'Reload')).not.toBeNull();
    expect((heading(host)!.querySelector('input') as HTMLInputElement).value).toBe('Mine');
  });

  it('edits a line in the Lines tab, then saves the change set from the save bar', async () => {
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001?tab=t-lines');
    await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
    await until(() => expect(text(host.querySelector('.g-t .n'))).toBe('120'));
    const qty = cellOf(rowsIn(host)[1], 'qty');
    click(qty);
    const input = qty.querySelector('input')!;
    input.value = '3';
    press(input, 'Enter');
    await until(() => expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes'));
    press(host.querySelector('.doc')!, 'Enter', { metaKey: true });
    await until(() => expect(requests(sent, 'PATCH', /order\/0000000000001$/)).toHaveLength(1));
    const body = requests(sent, 'PATCH', /order\/0000000000001$/)[0].body;
    expect(body.children.lines.update).toEqual([{ id: recorded.detail.body.children.lines[1].id, qty: '3.0000' }]);
    await until(() => expect(host.querySelector('.ev-savebar')).toBeNull());
    expect(location.search).toBe('?tab=t-lines');
  });

  it('switching tabs with unsaved edits asks nothing and keeps every edit for one save', async () => {
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001?tab=t-lines');
    await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
    const qty = cellOf(rowsIn(host)[1], 'qty');
    click(qty);
    const input = qty.querySelector('input')!;
    input.value = '3';
    press(input, 'Enter');
    press(document.activeElement!, 'Escape');
    await until(() => expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes'));
    click(byText(host, '.doc-tabs [role="tab"]', 'Details'));
    await until(() => expect(location.search).toBe(''));
    expect(document.querySelector('.modal')).toBeNull();
    expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes');
    click(byText(host, '.doc-tabs [role="tab"]', 'Lines120'));
    await until(() => expect(location.search).toBe('?tab=t-lines'));
    expect(document.querySelector('.modal')).toBeNull();
    await until(() => expect(text(cellOf(rowsIn(host)[1], 'qty'))).toContain('3'));
    press(host.querySelector('.doc')!, 'Enter', { metaKey: true });
    await until(() => expect(requests(sent, 'PATCH', /order\/0000000000001$/)).toHaveLength(1));
    expect(requests(sent, 'PATCH', /order\/0000000000001$/)[0].body.children.lines.update).toEqual([{ id: recorded.detail.body.children.lines[1].id, qty: '3.0000' }]);
  });

  it('still asks before opening another record with unsaved edits', async () => {
    const { host, router } = start(orderRoutes(), '/app/order/0000000000001?tab=t-lines');
    await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
    const qty = cellOf(rowsIn(host)[0], 'qty');
    click(qty);
    const input = qty.querySelector('input')!;
    input.value = '7';
    press(input, 'Enter');
    press(document.activeElement!, 'Escape');
    const leaving = router.go('record', { model: 'order', id: '2' });
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Discard unsaved changes?'));
    click(byText(document.body, '.modal-f button', 'Cancel'));
    expect(await leaving).toBe(false);
    expect(location.pathname).toBe('/app/order/0000000000001');
  });

  it('asks before leaving with unsaved lines, and stays when asked to keep editing', async () => {
    const { host, router } = start(orderRoutes(), '/app/order/0000000000001?tab=t-lines');
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
    expect(location.pathname).toBe('/app/order/0000000000001');
    const again = router.go('docs', {});
    await until(() => expect(document.querySelector('.modal')).not.toBeNull());
    click(byText(document.body, '.modal-f button', 'Discard changes'));
    expect(await again).toBe(true);
    await until(() => expect(location.pathname).toBe('/docs'));
  });

  it('runs an action with its scalar arguments through the action dialog', async () => {
    const { host, sent } = start([...orderRoutes(), ['POST', /\/api\/order\/0000000000001\/actions\/close$/, recorded.action]], '/app/order/0000000000001');
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
    const created = { status: 201, etag: '"new"', body: { id: id(56), title: 'Acme Trading', rate: '0.00', credit_days: 30 } };
    const { host, sent } = start([...adminRoutes(),
      ['POST', /\/api\/customer$/, created],
      ['GET', /\/api\/customer\/000000000001r$/, { ...created, status: 200 }],
    ], '/app/customer/new?title=Acme%20Trading');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    const name = host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!;
    expect(name.value).toBe('Acme Trading');
    expect(host.querySelector('.doc-side')).toBeNull();
    typeInto(host.querySelector<HTMLInputElement>('.fld[data-field="credit_days"] input')!, '30');
    click(byText(host, '.ev-savebar button', 'Create customer'));
    await until(() => expect(location.pathname).toBe('/app/customer/000000000001r'));
    expect(requests(sent, 'POST', /customer$/)[0].body).toMatchObject({ title: 'Acme Trading', credit_days: 30 });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toContain('Acme Trading'));
    expect(lastToast()).toContain('Customer created');
  });

  it('gives a new record the record title row: the model icon tile, Cancel and Create', async () => {
    const created = { status: 201, etag: '"new"', body: { id: id(56), title: 'Acme Trading', rate: '0.00', credit_days: 30 } };
    const { host, sent } = start([...adminRoutes(),
      ['POST', /\/api\/customer$/, created],
      ['GET', /\/api\/customer\/000000000001r$/, { ...created, status: 200 }],
    ], '/app/customer/new?title=Acme%20Trading');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    // No name yet, so no letter mark: the model's icon on a tinted tile.
    expect(host.querySelector('.doc-head .doc-mark .ph-icon svg')).not.toBeNull();
    expect(host.querySelector('.doc-head .doc-mark .org-mark')).toBeNull();
    const actions = [...host.querySelectorAll<HTMLButtonElement>('.doc-head .ph-actions button')];
    expect(actions.map(button => text(button))).toEqual(['Cancel', 'Create customer']);
    expect(actions[1].classList.contains('primary')).toBe(true);
    click(actions[1]);
    await until(() => expect(location.pathname).toBe('/app/customer/000000000001r'));
    expect(requests(sent, 'POST', /customer$/)).toHaveLength(1);
  });

  it('a naming series field says it is assigned on save, is never sent, and titles the saved record', async () => {
    const created = screen.quote_create, id = created.body.id;
    const { host, sent } = start([...adminRoutes(),
      ['POST', /\/api\/quote$/, created],
      ['GET', new RegExp(`/api/quote/${id}$`), screen.quote_detail],
      ['GET', /\/api\/customer\/1$/, { ...recorded.customer, status: 200 }],
    ], '/app/quote/new');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New quote'));
    const number = host.querySelector<HTMLInputElement>('.fld[data-field="number"] input')!;
    expect(number.disabled).toBe(true);
    expect(number.placeholder).toBe('Assigned on save');
    typeInto(host.querySelector<HTMLInputElement>('.fld[data-field="issued"] input')!, '20-10-2026');
    click(byText(host, '.ev-savebar button', 'Create quote'));
    await until(() => expect(location.pathname).toBe(`/app/quote/${id}`));
    expect(requests(sent, 'POST', /quote$/)[0].body).not.toHaveProperty('number');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('QT-2026-00001'));
    expect(host.querySelector('.doc-head h1')!.classList.contains('mono')).toBe(true);
    // The header shows the number, so the read form leaves it out (it can't be edited anyway).
    expect(field(host, 'number')).toBeNull();
  });
});

describe('permission-driven screens', () => {
  const viewer = (): Route[] => [
    ['GET', /\/api\/auth\/session$/, screen.viewer_session],
    ['GET', /\/api\/schema$/, screen.viewer_schema],
    ['GET', /\/api\/contact\?/, screen.viewer_contacts],
    ['GET', /\/api\/contact\/0000000000001$/, screen.viewer_contact_detail],
    ['GET', /\/api\/contact\/0000000000001\/attachments$/, screen.attachments],
  ];

  it('a viewer sees only readable fields, no New, no actions, and nothing editable', async () => {
    const { host } = start(viewer(), '/app/contact');
    await until(() => expect([...host.querySelectorAll('thead th')].map(text)).toEqual(['Full name', 'Email']));
    expect(byText(host, '.ph-actions button', /New contact/)).toBeNull();
    expect(host.querySelector('.topbar .btn.primary')).toBeNull();
    // No delete and no State: rows are not selectable.
    expect(host.querySelector('tbody [role="checkbox"]')).toBeNull();
    click(host.querySelector('a.ev-row-link'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Mariam Haddad'));
    expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['email']);
    expect(host.querySelector('.fs button.fval')).toBeNull();
    expect(host.querySelector('.doc-head button.fval')).toBeNull();
    expect(byText(host, '.doc-head button', 'Mark contacted')).toBeNull();
    // The viewer reads the file an administrator attached, and cannot add one.
    await until(() => expect(text(byText(host, '.side-sec', /^Files/))).toContain('brief.txt'));
    expect(host.querySelector('.ev-upload')).toBeNull();
    expect(host.querySelector('.ev-file-remove')).toBeNull();
  });

  it('offers no remove on a read-only model, even to an administrator', async () => {
    const schema = copy(screen.schema);
    schema.body.models.find((model: any) => model.name === 'contact').api = 'read';
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/contact\/0000000000001$/, screen.contact_detail],
      ['GET', /\/api\/contact\/0000000000001\/attachments$/, screen.attachments]], '/app/contact/0000000000001');
    await until(() => expect(text(byText(host, '.side-sec', /^Files/))).toContain('brief.txt'));
    expect(host.querySelector('.ev-file-remove')).toBeNull();
    expect(host.querySelector('.ev-upload')).toBeNull();
  });

  it('an editor removes a file after confirming; cancelling sends nothing; a refusal says why', async () => {
    let items = [{ id: 1, name: 'brief.txt', size: 5 }, { id: 2, name: 'scan.pdf', size: 2048 }];
    let refuse = false;
    const { host, sent } = start([...adminRoutes(),
      ['GET', /\/api\/contact\/0000000000001$/, screen.contact_detail],
      ['GET', /\/api\/contact\/0000000000001\/attachments$/, () => ({ status: 200, etag: null, body: { items: copy(items) } })],
      ['DELETE', /\/api\/contact\/0000000000001\/attachments\/\d+$/, request => {
        if (refuse) return { status: 409, etag: null, body: { error: 'This record is locked' } };
        const gone = Number(request.url.split('/').pop());
        items = items.filter(item => item.id !== gone);
        return { status: 204, etag: null, body: null };
      }],
    ], '/app/contact/0000000000001');
    const files = () => text(byText(host, '.side-sec', /^Files/));
    const removeOf = (name: string) => host.querySelector<HTMLButtonElement>(`.ev-file-remove[aria-label="Remove ${name}"]`);
    await until(() => expect(removeOf('brief.txt')).not.toBeNull());
    expect(removeOf('scan.pdf')).not.toBeNull();
    // Cancel: nothing is sent and the file stays.
    click(removeOf('brief.txt'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Remove brief.txt?'));
    expect(text(document.querySelector('.modal'))).toContain('This can’t be undone.');
    click(byText(document.body, '.modal-f button', 'Cancel'));
    await until(() => expect(document.querySelector('.modal')).toBeNull());
    expect(requests(sent, 'DELETE', /attachments/)).toHaveLength(0);
    expect(files()).toContain('brief.txt');
    // Confirm: DELETE, the row goes, a toast, and focus lands on the remove button now in its place.
    click(removeOf('brief.txt'));
    await until(() => expect(document.querySelector('.modal')).not.toBeNull());
    click(byText(document.body, '.modal-f button', 'Remove'));
    await until(() => expect(requests(sent, 'DELETE', /attachments\/1$/)).toHaveLength(1));
    await until(() => expect(files()).not.toContain('brief.txt'));
    expect(files()).toContain('scan.pdf');
    expect(lastToast()).toBe('brief.txt removed');
    await until(() => expect(document.activeElement).toBe(removeOf('scan.pdf')));
    // A server refusal shows its message.
    refuse = true;
    click(removeOf('scan.pdf'));
    await until(() => expect(document.querySelector('.modal')).not.toBeNull());
    click(byText(document.body, '.modal-f button', 'Remove'));
    await until(() => expect(lastToast()).toBe('Not removed: This record is locked'));
    expect(files()).toContain('scan.pdf');
  });

  it('the admin of the same records gets every field, the action and an upload; a refused upload says why', async () => {
    let uploaded = false;
    const { host, sent } = start([...adminRoutes(),
      ['GET', /\/api\/contact\/0000000000001$/, screen.contact_detail],
      ['GET', /\/api\/contact\/0000000000001\/attachments$/, () => copy(uploaded ? screen.attachments : screen.attachments_empty)],
      ['POST', /\/api\/contact\/0000000000001\/attachments$/, sent => {
        if (sent.body.name !== 'brief.txt') return copy(screen.attachment_refused);
        uploaded = true;
        return copy(screen.attachment_upload);
      }],
    ], '/app/contact/0000000000001');
    await until(() => expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['email', 'phone', 'notes']));
    expect(host.querySelectorAll('.fs button.fval').length).toBe(3);
    expect(host.querySelector('.doc-head .doc-title button.fval')).not.toBeNull();
    expect(byText(host, '.doc-head button', 'Mark contacted')).not.toBeNull();
    const upload = host.querySelector<HTMLInputElement>('.ev-upload input')!;
    Object.defineProperty(upload, 'files', { value: [new File(['Hello'], 'brief.txt', { type: 'text/plain' })], configurable: true });
    upload.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(requests(sent, 'POST', /attachments$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /attachments$/)[0].body).toEqual({ name: 'brief.txt', content: 'SGVsbG8=' });
    await until(() => expect(lastToast()).toBe('brief.txt attached'));
    await until(() => expect(text(byText(host, '.side-sec', /^Files/))).toContain('brief.txt'));
    const again = host.querySelector<HTMLInputElement>('.ev-upload input')!;
    Object.defineProperty(again, 'files', { value: [new File(['%PDF'], 'scan.pdf', { type: 'application/pdf' })], configurable: true });
    again.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(lastToast()).toBe('Not attached: Use a nonempty file up to 1 MiB'));
  });
});

describe('printing a record', () => {
  // The schema the server sends when the role may print contacts and the print extra is installed.
  const printing = (available = true, prints = [{ name: 'ContactPrint', title: 'Contact sheet', default: true }, { name: 'LabelPrint', title: 'Label', default: false }]) => {
    const schema = copy(screen.schema);
    schema.body.printing = { available };
    const contact = schema.body.models.find((model: any) => model.name === 'contact');
    if (prints.length) contact.prints = prints;
    return schema;
  };
  const routes = (schema: any, filed: () => void = () => {}): Route[] => [
    ['GET', /\/api\/auth\/session$/, recorded.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', /\/api\/contact\/0000000000001$/, screen.contact_detail],
    ['GET', /\/api\/contact\/0000000000001\/attachments$/, screen.attachments_empty],
    ['POST', /\/api\/contact\/0000000000001\/prints\/LabelPrint\/file$/, () => { filed(); return { status: 201, body: { id: 7, name: 'Mariam Haddad.pdf', size: 15000 }, headers: {} } as any; }],
  ];

  it('opens the server PDF of the chosen design, with Download and File a copy', async () => {
    let filed = 0;
    const { host, sent } = start(routes(printing(), () => { filed += 1; }), '/app/contact/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Print')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Print'));
    const drawer = () => document.querySelector<HTMLElement>('.drawer.ev-print')!;
    await until(() => expect(drawer()).not.toBeNull());
    const frame = drawer().querySelector('iframe')!;
    expect(frame.getAttribute('src')).toBe('/api/contact/0000000000001/prints/ContactPrint.pdf');
    expect(drawer().querySelector('a.btn.primary')!.getAttribute('href')).toBe('/api/contact/0000000000001/prints/ContactPrint.pdf?download=1');
    const select = drawer().querySelector('select')!;
    expect([...select.options].map(option => option.textContent)).toEqual(['Contact sheet', 'Label']);
    select.value = 'LabelPrint';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(frame.getAttribute('src')).toBe('/api/contact/0000000000001/prints/LabelPrint.pdf'));
    click(byText(drawer(), 'button', 'File a copy'));
    await until(() => expect(filed).toBe(1));
    expect(requests(sent, 'POST', /prints\/LabelPrint\/file$/)[0].headers['x-evlet-request']).toBe('1');
    await until(() => expect(lastToast()).toBe('Mariam Haddad.pdf filed with this contact'));
    // The Files block loads again for the new attachment.
    await until(() => expect(requests(sent, 'GET', /attachments$/).length).toBeGreaterThan(1));
  });

  it('shows no Print without designs for the role or without the print extra', async () => {
    for (const schema of [printing(false), printing(true, [])]) {
      const { host } = start(routes(schema), '/app/contact/0000000000001');
      await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Mariam Haddad'));
      expect(byText(host, '.doc-head button', 'Print')).toBeNull();
      stopAll();
    }
  });
});

describe('Record info times', () => {
  afterEach(() => { try { localStorage.clear(); } catch { /* no storage */ } });
  const info = (host: Element) => byText(host, '.side-sec', /^Record info/)!;
  const rows = (host: Element) => {
    const block = info(host), terms = [...block.querySelectorAll('dt')].map(text);
    return Object.fromEntries([...block.querySelectorAll('dd')].map((dd, index) => [terms[index], text(dd)]));
  };

  it('shows Created and Last modified relative to now, with who, and switches every time to exact on a click', async () => {
    // Recorded at 06:02 UTC; two hours later here.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-04T08:02:30Z'));
    const { host } = start([...orderRoutes(), ['GET', /\/api\/_settings\/me$/, screen.settings_me]], '/app/order/0000000000001', { storage: localStorage });
    await until(() => expect(rows(host)['Created']).toBe('2 hours ago · admin'));
    expect(rows(host)['Last modified']).toBe('2 hours ago · admin');
    const times = [...info(host).querySelectorAll<HTMLButtonElement>('button.ev-time-toggle')];
    expect(times.map(button => button.querySelector('time')!.getAttribute('datetime'))).toEqual(['2026-11-04T06:02:00.000Z', '2026-11-04T06:02:00.000Z']);
    expect(times[0].getAttribute('aria-label')).toBe('2 hours ago, 04-11-2026 10:02 GST. Show exact times');
    // Who links to Controls Users for a person who may open Controls.
    await until(() => expect(info(host).querySelectorAll('dd a.link-q[href="/controls/users"]').length).toBe(2));
    click(times[0]);
    await until(() => expect(rows(host)['Created']).toBe('04-11-2026 10:02 GST · admin'));
    expect(rows(host)['Last modified']).toBe('04-11-2026 10:02 GST · admin');
    expect(times.map(button => button.getAttribute('aria-pressed'))).toEqual(['true', 'true']);
    expect(localStorage.getItem('evlet.admin.exactTimes')).toBe('true');
  });

  it('opens with exact times when the person chose them before', async () => {
    localStorage.setItem('evlet.admin.exactTimes', 'true');
    const { host } = start(orderRoutes(), '/app/order/0000000000001', { storage: localStorage });
    await until(() => expect(rows(host)['Created']).toBe('04-11-2026 10:02 GST · admin'));
    // Without Controls, who is plain text.
    expect(info(host).querySelector('dd a.link-q[href="/controls/users"]')).toBeNull();
  });
});


describe('each fact shows once on the record page', () => {
  const withOrder = (patch: Record<string, unknown>): Route[] => {
    const schema = copy(screen.schema);
    Object.assign(schema.body.models.find((model: { name: string }) => model.name === 'order'), patch);
    return [['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ...orderRoutes().slice(2)];
  };
  const stats = (host: Element) => [...host.querySelectorAll('.sum-t > span')].map(text);
  const blocks = (host: Element) => [...host.querySelectorAll('.side-sec h4')].map(item => text(item).replace(/All fields$/, ''));

  it('E1: key fields show in the header facts, and the summary keeps only what the header does not show', async () => {
    const { host } = start(withOrder({ key_fields: ['customer', 'total'] }), '/app/order/0000000000001');
    await until(() => expect(stats(host)).toEqual(['Due', 'Line count']));
    expect([...host.querySelectorAll('.rec-meta .ev-fact')].map(item => item.getAttribute('title'))).toEqual(['Customer', 'Total']);
  });

  it('E1, E6: no summary strip when nothing is left to show, or when Meta.summary is False', async () => {
    const { host } = start(withOrder({ key_fields: ['customer', 'due', 'total'], fields: copy(screen.schema).body.models.find((model: { name: string }) => model.name === 'order').fields
      .map((field: { name: string }) => field.name === 'line_count' ? { ...field, compute: null } : field) }), '/app/order/0000000000001');
    await until(() => expect(host.querySelectorAll('.rec-meta .ev-fact')).toHaveLength(3));
    expect(host.querySelector('.sum')).toBeNull();
    stopAll();
    const off = start(withOrder({ summary: false }), '/app/order/0000000000001');
    await until(() => expect(text(off.host.querySelector('.doc-head h1'))).toContain('Big order'));
    await until(() => expect(blocks(off.host)).toContain('Record info'));
    expect(off.host.querySelector('.sum')).toBeNull();
  });

  it('E2: the Key fields block shows only while another tab than Details is open', async () => {
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(blocks(host)).toContain('Record info'));
    expect(blocks(host)).not.toContain('Key fields');
    click(byText(host, '.doc-tabs [role="tab"]', /^Lines/));
    await until(() => expect(blocks(host)[0]).toBe('Key fields'));
    click(byText(host, '.side-sec button', 'All fields'));
    await until(() => expect(blocks(host)).not.toContain('Key fields'));
    expect(byText(host, '.doc-tabs [role="tab"]', 'Details')!.getAttribute('aria-selected')).toBe('true');
  });

  it('E3: the read form leaves out the title the header edits; a new record keeps it in the form', async () => {
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(field(host, 'customer')).not.toBeNull());
    expect(field(host, 'title')).toBeNull();
    expect(heading(host)!.querySelector('button.fval')).not.toBeNull();
    stopAll();
    const created = start(adminRoutes(), '/app/order/new');
    await until(() => expect(text(created.host.querySelector('.doc-head h1'))).toBe('New order'));
    expect(created.host.querySelector('.fld[data-field="title"] input')).not.toBeNull();
  });

  it('E4: Record info keeps the times, and More copies the record ID', async () => {
    const writes: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (value: string) => { writes.push(value); } }, configurable: true });
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(text(byText(host, '.side-sec', /^Record info/))).toContain('Created'));
    expect([...byText(host, '.side-sec', /^Record info/)!.querySelectorAll('dt')].map(text)).not.toContain('ID');
    click(host.querySelector('.doc-head button[aria-label="More actions"]'));
    await until(() => expect(byText(document.body, '[role="menuitem"]', 'Copy record ID')).not.toBeNull());
    click(byText(document.body, '[role="menuitem"]', 'Copy record ID'));
    await until(() => expect(writes).toEqual(['0000000000001']));
    await until(() => expect(lastToast()).toBe('Copied the record ID'));
  });

  const customer = (orders: number, quotes: number, patch: Record<string, unknown> = {}): Route[] => {
    const schema = copy(screen.schema);
    Object.assign(schema.body.models.find((model: { name: string }) => model.name === 'customer'), patch);
    const page = (total: number) => ({ status: 200, etag: null, body: { items: [], total, limit: 10, offset: 0 } });
    return [['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
      ['GET', /\/api\/customer\/0000000000001\/related\/order\.customer/, page(orders)], ['GET', /\/api\/customer\/0000000000001\/related\/quote\.customer/, page(quotes)]];
  };

  it('E4: Record info is left out while it has nothing to show', async () => {
    const detail = copy(recorded.detail);
    delete detail.body._audit;
    const { host } = start([...orderRoutes().filter(route => !String(route[1]).includes('order\\/0000000000001$')), ['GET', /\/api\/order\/0000000000001$/, detail]], '/app/order/0000000000001?tab=t-lines');
    await until(() => expect(blocks(host)).toContain('Key fields'));
    expect(blocks(host)).not.toContain('Record info');
  });

  it('E5: the Related block lists only relations with records, and goes when none has any', async () => {
    const { host } = start(customer(2, 0), '/app/customer/0000000000001');
    await until(() => expect([...host.querySelectorAll('.side-sec .rel-row .rel-nm')].map(text)).toEqual(['Orders']));
    expect(text(byText(host, '.side-sec', /^Related/))).not.toContain('Quotes');
    stopAll();
    const empty = start(customer(0, 0), '/app/customer/0000000000001');
    await until(() => expect(blocks(empty.host)).toContain('Record info'));
    await until(() => expect(blocks(empty.host)).not.toContain('Related'));
    // The Related tab still lists both.
    click(byText(empty.host, '.doc-tabs [role="tab"]', 'Related'));
    await until(() => expect(text(empty.host.querySelector('.ev-related[aria-label="Quotes"] .ev-related-empty'))).toBe('No quotes yet.'));
  });

  it('E6: Meta.side_blocks picks and orders the side blocks', async () => {
    const { host } = start(customer(2, 0, { side_blocks: ['info', 'related'] }), '/app/customer/0000000000001');
    await until(() => expect(blocks(host)).toEqual(['Record info', 'Related']));
    stopAll();
    const none = start(customer(2, 0, { side_blocks: [] }), '/app/customer/0000000000001');
    await until(() => expect(text(none.host.querySelector('.doc-head h1'))).not.toBe(''));
    expect(blocks(none.host)).toEqual([]);
  });
});

describe('a saved record with Tags, MultiSelect and Reference values', () => {
  // These values are lists and objects. Opening the record once read them as empty, so the record looked
  // edited and the next save of any field sent nulls for them and cleared them.
  const values = { tags: ['vip', 'gulf'], kinds: ['site_visit'], about: { model: 'customer', id: 1 } };
  const routes = (): Route[] => {
    const schema = copy(screen.schema);
    const order = schema.body.models.find((model: any) => model.name === 'order');
    const remark = order.fields.find((item: any) => item.name === 'remark');
    const types = { tags: 'tags', kinds: 'multiselect', about: 'reference' } as const;
    for (const [name, type] of Object.entries(types)) {
      order.fields.push({ ...remark, name, label: name, type, locked_when: null, max_length: null, ...(type === 'multiselect' ? { choices: ['site_visit', 'remote'] } : {}) });
      order.layout[0].fields.push(name);
      order.calculation.plan.tables.$root.fields[name] = { computed: false, default: null, kind: type, maximum: null, minimum: null, required: false, scale: null, stored: true, type };
    }
    order.calculation.plan.model_schema_hash = fingerprint(order.calculation.plan);
    const detail = copy(recorded.detail);
    Object.assign(detail.body, values);
    return [
      ['GET', /\/api\/auth\/session$/, recorded.session],
      ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/order\/0000000000001$/, detail],
      ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
      ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
      ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
      ['PATCH', /\/api\/order\/0000000000001$/, recorded.update],
    ];
  };

  it('opens unchanged, shows the values, and a save of another field leaves them alone', async () => {
    const { host, sent } = start(routes(), '/app/order/0000000000001');
    await until(() => expect(heading(host)?.querySelector('button.fval')).toBeTruthy());
    await until(() => expect(text(field(host, 'tags'))).toContain('vip'));
    expect(text(field(host, 'tags'))).toContain('gulf');
    expect(text(field(host, 'kinds'))).toContain('Site visit');
    expect(text(field(host, 'about'))).toContain('Customer #1');
    expect(host.querySelector('.ev-savebar')).toBeNull();
    expect(text(host)).not.toContain('Unsaved changes');

    click(heading(host)!.querySelector('button.fval'));
    const input = heading(host)!.querySelector('input')!;
    input.value = 'Bigger order';
    press(input, 'Enter');
    await until(() => expect(requests(sent, 'PATCH', /order\/0000000000001$/)).toHaveLength(1));
    const body = requests(sent, 'PATCH', /order\/0000000000001$/)[0].body;
    expect(body.title).toBe('Bigger order');
    for (const name of Object.keys(values)) expect(body, name).not.toHaveProperty(name);
    expect(Object.keys(body).filter(key => !key.startsWith('_'))).toEqual(['title']);
  });
});
