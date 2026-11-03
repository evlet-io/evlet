// Controls Print designs in jsdom: app designs are read only and copy into a stored design; blocks are added,
// moved and switched to HTML; the watermark size goes to the server preview; Save sends the definition.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { server, type Sent } from './server';
import { byText, click, lastToast, stopAll, text, until } from './screens-helpers';

const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [],
  roles: [{ id: 'admin', kind: 'framework', base: null, realm: 'desk', opens_controls: true, description: '', users: 1, app: null, declared: true }],
  models: [], scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
  users: { active: 1, total: 1 }, audit: [], two_step: false, links: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
});
const definition = () => ({
  title: 'Invoice', default: true, model: 'invoice',
  page: { size: 'A4', orientation: 'portrait', margins: ['30mm', '14mm', '20mm', '14mm'], first_margin_top: null, font_size: '9pt', currency: null, date_format: '%d-%m-%Y' },
  header: [{ type: 'text', text: 'Acme · {{ doc.name }}', weight: 'bold' }], header_first: null,
  main: [{ type: 'heading', text: 'Invoice' }, { type: 'fields', names: [['number', 'Invoice no.'], ['customer', null]], columns: 2 }],
  footer: [{ type: 'pagenumber', format: 'Page {page} of {pages}', align: 'right' }],
  footnote: { place: 'last_page', height: null, blocks: [{ type: 'text', text: 'Received by' }] },
  watermark: { text: 'DRAFT', size: 55, opacity: 0.1, rotate: -30, show_if: "doc.status == 'draft'" },
});
const listing = () => ({
  available: true, blocks: ['heading', 'text', 'fields', 'table', 'totals', 'image', 'spacer', 'pagebreak', 'pagenumber'],
  designs: [{ name: 'InvoicePrint', source: 'app', definition: definition(), changed_by: null, changed_at: null }],
  models: [{ name: 'invoice', label: 'Invoice', attachments: false, tables: [], fields: [{ name: 'number', label: 'Number', kind: 'text' }, { name: 'customer', label: 'Customer', kind: 'link' }] }],
  reports: [],
});

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

function open() {
  window.history.replaceState(null, '', '/controls/prints');
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
    ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(), etag: null })],
    ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: { version: 1, problems: [], items: [] }, etag: null })],
    ['GET', /\/api\/_controls\/prints$/, () => ({ status: 200, body: listing(), etag: null })],
    ['GET', /\/api\/invoice\?/, () => ({ status: 200, body: { items: [{ id: 7 }], total: 1, limit: 1, offset: 0 }, etag: null })],
    ['POST', /\/api\/_controls\/prints\/preview$/, () => ({ status: 200, body: { url: '/api/_controls/prints/preview/abcdefghijklmnopqrstuvwx.pdf', size: 9000 }, etag: null })],
    ['PUT', /\/api\/_controls\/prints\/InvoicePrintCopy$/, (sent: Sent) => ({ status: 200, body: { name: 'InvoicePrintCopy', source: 'stored', definition: sent.body.definition }, etag: null })],
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent };
}
const posted = (sent: Sent[], pattern: RegExp) => sent.filter(item => item.method !== 'GET' && pattern.test(item.url));

describe('print designs', () => {
  it('previews the app design read only, copies it, edits blocks and the watermark, and saves', { timeout: 20000 }, async () => {
    const { host, sent } = open();
    await until(() => expect(host.querySelector('.ev-pd-editor')).not.toBeNull());
    expect(text(host.querySelector('.ev-pd-list'))).toContain('InvoicePrint');
    // The app's design: read only, previewed against the first record.
    await until(() => expect(host.querySelector('.ev-pd-frame')?.getAttribute('src')).toBe('/api/_controls/prints/preview/abcdefghijklmnopqrstuvwx.pdf'));
    expect(posted(sent, /preview$/)[0].body).toMatchObject({ record: 7, definition: { model: 'invoice' } });
    expect((host.querySelector('#pd-title') as HTMLInputElement).disabled).toBe(true);
    expect(byText(host, '.ev-pd-editor button', 'Save')).toBeNull();
    click(byText(host, '.ev-pd-editor button', 'Copy'));
    await until(() => expect((host.querySelector('#pd-name') as HTMLInputElement)?.value).toBe('InvoicePrintCopy'));
    expect((host.querySelector('#pd-title') as HTMLInputElement).value).toBe('Invoice (copy)');
    // Main region: select the heading, switch it to HTML.
    const items = () => [...host.querySelectorAll('.ev-pd-items li')];
    expect(items().map(item => text(item.querySelector('b')))).toEqual(['Heading', 'Fields']);
    click(items()[0].querySelector('button.grow'));
    const html = host.querySelector<HTMLInputElement>('.ev-pd-props .ev-pd-switch input')!;
    html.checked = true;
    html.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(host.querySelector<HTMLTextAreaElement>('#pd-html')?.value).toBe('<p>Invoice</p>'));
    // Move the fields above the heading.
    click(items()[1].querySelector('button[aria-label="Move up"]'));
    await until(() => expect(items().map(item => text(item.querySelector('b')))).toEqual(['Fields', 'Heading']));
    // Watermark: a bigger size reaches the next preview.
    click(byText(host, '.ev-pd-regions button', 'Watermark'));
    await until(() => expect(host.querySelector('#pd-wm-size')).not.toBeNull());
    const size = host.querySelector<HTMLInputElement>('#pd-wm-size')!;
    size.value = '90';
    size.dispatchEvent(new Event('input', { bubbles: true }));
    await until(() => expect(posted(sent, /preview$/).at(-1)!.body.definition.watermark.size).toBe(90));
    click(byText(host, '.ev-pd-editor button', 'Save'));
    await until(() => expect(posted(sent, /InvoicePrintCopy$/)).toHaveLength(1));
    const saved = posted(sent, /InvoicePrintCopy$/)[0].body.definition;
    expect(saved).toMatchObject({ title: 'Invoice (copy)', default: false, watermark: { size: 90 } });
    expect(saved.main.map((block: any) => block.type)).toEqual(['fields', 'heading']);
    expect(saved.main[1].html).toBe('<p>Invoice</p>');
    await until(() => expect(lastToast()).toBe('Invoice (copy) saved'));
  });
});
