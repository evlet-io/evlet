import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { byText, click, lastToast, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';

// Responses recorded from the real report routes (record_reports in scripts/generate_ui_framework_fixtures.py):
// CustomerRates, a manual report with two filters, a link column, a total over every row and a bar chart.
const customer = (id: number, name: string) => ({ status: 200, etag: null, body: { id, name, rate: '0.00', credit_days: 30 } });
const routes = (extra: Route[] = [], schema = screen.report_schema): Route[] => [
  ...extra,
  ['GET', /\/api\/auth\/session$/, screen.report_session],
  ['GET', /\/api\/schema$/, schema],
  ['POST', /\/api\/_reports\/CustomerRates\/evaluate$/, screen.report_evaluate],
  ['POST', /\/api\/_reports\/CustomerRates$/, sent => sent.body.sort === '-rate' ? screen.report_run_sorted : screen.report_run],
  ['GET', /\/api\/customer\/1$/, customer(1, 'Corniche Foods')],
  ['GET', /\/api\/customer\/2$/, customer(2, 'Al Noor Trading')],
  ['GET', /\/api\/customer\/3$/, customer(3, 'Blue Dune Interiors')],
  ['GET', /\/api\/customer\/4$/, customer(4, 'Oasis Water')],
  ['POST', /\/api\/_reports\/RatesByTerms\/evaluate$/, screen.report_evaluate],
  ['POST', /\/api\/_reports\/RatesByTerms$/, screen.report_grouped_run],
  ['GET', /\/api\/customer\?limit=\d+&offset=0$/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }],
];
const page = () => document.querySelector<HTMLElement>('.ev-report')!;
const field = (name: string) => page().querySelector<HTMLElement>(`.fld[data-field="${name}"]`)!;
const cells = () => [...page().querySelectorAll('tbody tr:not(.spacer)')].map(row => [...row.querySelectorAll('td')].map(text));
const run = () => click(byText(page(), 'button', 'Run'));

beforeEach(() => {
  // jsdom has no object URLs; the page only hands them to a link or a frame.
  (URL as any).createObjectURL = vi.fn(() => 'blob:report-file');
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => stopAll());

describe('reports', () => {
  it('lists the reports a role may run in the sidebar and on /reports', async () => {
    const { host } = start(routes(), '/reports');
    await until(() => expect(byText(host, '.ev-report-card', /Customer rates/)).not.toBeNull());
    expect(text(host.querySelector('.ev-report-group h2'))).toBe('Sales');
    expect(text(byText(host, '.ev-report-card', /Customer rates/))).toContain('4 columns · 2 filters');
    const nav = byText<HTMLAnchorElement>(host, '.sidebar .nav-item', 'Customer rates')!;
    expect(nav.getAttribute('href')).toBe('/reports/CustomerRates');
    click(byText(host, '.ev-report-card', /Customer rates/));
    await until(() => expect(location.pathname).toBe('/reports/CustomerRates'));
  });

  it('checks filters on the server and runs a manual report only on Run', async () => {
    const { sent } = start(routes(), '/reports/CustomerRates');
    await until(() => expect(page()).not.toBeNull());
    await until(() => expect(requests(sent, 'POST', /evaluate$/).length).toBe(1));
    expect(text(page().querySelector('.empty h3'))).toBe('Set the filters, then run');
    typeInto(field('min_days').querySelector('input')!, '25');
    await until(() => expect(requests(sent, 'POST', /evaluate$/).at(-1)!.body.filters).toEqual({ min_days: 25, search: '' }));
    expect(requests(sent, 'POST', /CustomerRates$/)).toHaveLength(0);
    run();
    await until(() => expect(cells()).toHaveLength(3));
    const body = requests(sent, 'POST', /CustomerRates$/)[0].body;
    expect(body).toMatchObject({ filters: { min_days: 25, search: '' }, limit: 50, offset: 0, sort: null });
    // Link cells show the record title and open the record; numbers carry the report's currency.
    await until(() => expect(cells()[0][0]).toBe('Al Noor Trading'));
    expect(page().querySelector('tbody a.ev-row-link')!.getAttribute('href')).toBe('/app/customer/2');
    expect(cells()[0].slice(1)).toEqual(['Al Noor Trading', '45', 'AED 480.50']);
    expect(text(page().querySelector('tfoot'))).toContain('AED 2,040.50');
    expect(text(page().querySelector('tfoot'))).toContain('Total');
    // The filters it ran with, and the address that opens the same view.
    expect(text(page().querySelector('.ev-report-applied'))).toBe('Credit days from 25');
    expect(location.search).toBe('?min_days=25');
    expect(page().querySelectorAll('.ev-report-chart rect')).toHaveLength(3);
  });

  it('says when the filters changed after a run, and sorts and pages with the filters it ran with', async () => {
    const { sent } = start(routes(), '/reports/CustomerRates?min_days=20');
    await until(() => expect(cells()).toHaveLength(3));
    typeInto(field('search').querySelector('input')!, 'Noor');
    await until(() => expect(page().querySelector<HTMLElement>('.ev-report-changed')!.hidden).toBe(false));
    // Rate is sortable; Credit days is not.
    expect(page().querySelector('th[data-key="credit_days"]')!.getAttribute('aria-sort')).toBeNull();
    click(page().querySelector('th[data-key="rate"]'));
    click(page().querySelector('th[data-key="rate"]'));
    await until(() => expect(requests(sent, 'POST', /CustomerRates$/).at(-1)!.body.sort).toBe('-rate'));
    expect(requests(sent, 'POST', /CustomerRates$/).at(-1)!.body.filters).toEqual({ min_days: 20, search: '' });
    await until(() => expect(location.search).toBe('?min_days=20&sort=-rate'));
  });

  it('opens a shared link with its filters and sort and runs it', async () => {
    const { sent } = start(routes(), '/reports/CustomerRates?min_days=20&sort=-rate');
    await until(() => expect(cells()).toHaveLength(3));
    expect(requests(sent, 'POST', /CustomerRates$/)[0].body).toMatchObject({ filters: { min_days: 20, search: '' }, sort: '-rate' });
    expect((field('min_days').querySelector('input') as HTMLInputElement).value).toBe('20');
    expect(page().querySelector('th[data-key="rate"]')!.getAttribute('aria-sort')).toBe('descending');
  });

  it('shows a filter the server refuses and does not run', async () => {
    const { sent } = start(routes([['POST', /evaluate$/, screen.report_evaluate_invalid]]), '/reports/CustomerRates');
    await until(() => expect(requests(sent, 'POST', /evaluate$/).length).toBe(1));
    run();
    await until(() => expect(text(field('min_days').querySelector('.err'))).toBe('Expected an integer'));
    expect(requests(sent, 'POST', /CustomerRates$/)).toHaveLength(0);
  });

  it('exports CSV with the filters and sort it ran with', async () => {
    const { sent } = start(routes([['POST', /export\.csv$/, { status: 200, etag: null, body: 'Customer,Name' }]]), '/reports/CustomerRates?min_days=20&sort=-rate');
    await until(() => expect(cells()).toHaveLength(3));
    click(byText(page(), '.ph button', 'Export'));
    await until(() => expect(lastToast()).toBe('Exported Customer rates'));
    expect(requests(sent, 'POST', /export\.csv$/)[0].body).toEqual({ filters: { min_days: 20, search: '' }, sort: '-rate' });
  });

  it('prints through Print Design when the role has a design and the server can print', async () => {
    const schema = copy(screen.report_schema);
    schema.body.reports[0].prints = [{ name: 'CustomerRatesPrint', title: 'Customer rates', default: true }];
    expect(byText(start(routes([], schema), '/reports/CustomerRates?min_days=20').host, '.ph button', 'Print')).toBeNull();
    stopAll();
    schema.body.printing = { available: true };
    const { sent } = start(routes([['POST', /prints\/CustomerRatesPrint$/, { status: 200, etag: null, body: '%PDF' }]], schema), '/reports/CustomerRates?min_days=20');
    await until(() => expect(cells()).toHaveLength(3));
    click(byText(page(), '.ph button', 'Print'));
    await until(() => expect(document.querySelector<HTMLIFrameElement>('.ev-print-frame')?.getAttribute('src')).toBe('blob:report-file'));
    expect(requests(sent, 'POST', /prints\/CustomerRatesPrint$/)[0].body).toEqual({ filters: { min_days: 20, search: '' } });
  });

  it('chooses columns per person and keeps the first one', async () => {
    start(routes(), '/reports/CustomerRates?min_days=20');
    await until(() => expect(cells()).toHaveLength(3));
    click(byText(page(), 'button', 'Columns'));
    const chooser = document.querySelector<HTMLElement>('.ev-columns')!;
    expect(byText(chooser, 'button', 'Customer')!.hasAttribute('disabled')).toBe(true);
    click(byText(chooser, 'button', 'Credit days'));
    await until(() => expect(page().querySelector('th[data-key="credit_days"]')).toBeNull());
    click(byText(chooser, 'button', 'Chart'));
    await until(() => expect(page().querySelector('.ev-report-chart')).toBeNull());
  });

  it('offers a model\'s reports from its list', async () => {
    const { host } = start(routes(), '/app/customer');
    await until(() => expect(byText(host, '.ph button', 'Reports')).not.toBeNull());
    click(byText(host, '.ph button', 'Reports'));
    click(byText(document.body, '[role="menuitem"]', 'Customer rates'));
    await until(() => expect(location.pathname).toBe('/reports/CustomerRates'));
  });

  it('puts a heading and a subtotal around each group', async () => {
    start(routes(), '/reports/RatesByTerms?min_days=20');
    await until(() => expect(page().querySelectorAll('tr.ev-report-head')).toHaveLength(2));
    const shown = () => [...page().querySelectorAll('tbody tr:not(.spacer)')].map(row => [row.className.replace(/\s*sel\b/, '').trim(), [...row.querySelectorAll('td')].map(text).filter(Boolean).join(' | ')]);
    await until(() => expect(shown()[1][1]).toContain('Corniche Foods'));
    expect(shown().map(([kind]) => kind)).toEqual(['ev-report-head', '', '', 'ev-report-sub', 'ev-report-head', '', 'ev-report-sub']);
    expect(shown()[0][1]).toBe('30 · 2 rows');
    expect(shown()[3][1]).toBe('Subtotal | AED 1,560.00');
    expect(shown()[6][1]).toBe('Subtotal | AED 480.50');
  });

  it('saves a view of the filters and sort, and runs it again', async () => {
    const storage = new Map<string, string>();
    const store = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => void storage.set(key, value),
      removeItem: (key: string) => void storage.delete(key) } as unknown as Storage;
    const { sent } = start(routes(), '/reports/CustomerRates?min_days=20&sort=-rate', { storage: store });
    await until(() => expect(cells()).toHaveLength(3));
    click(byText(page(), '.ev-report-views button', 'Save view'));
    const dialog = document.querySelector<HTMLElement>('.modal')!;
    typeInto(dialog.querySelector('input')!, 'Long terms');
    click(byText(dialog, 'button', 'Save view'));
    await until(() => expect(byText(page(), '.ev-report-view button', 'Long terms')).not.toBeNull());
    expect([...storage.values()].some(value => value.includes('"name":"Long terms"') && value.includes('"min_days":"20"') && value.includes('"sort":"-rate"'))).toBe(true);
    // Change the filters and sort, then the view brings back what it saved.
    typeInto(field('min_days').querySelector('input')!, '40');
    click(page().querySelector('th[data-key="rate"]'));
    const before = requests(sent, 'POST', /CustomerRates$/).length;
    click(byText(page(), '.ev-report-view button', 'Long terms'));
    await until(() => expect(requests(sent, 'POST', /CustomerRates$/).length).toBeGreaterThan(before + 1));
    expect(requests(sent, 'POST', /CustomerRates$/).at(-1)!.body).toMatchObject({ filters: { min_days: 20, search: '' }, sort: '-rate' });
    click(page().querySelector('.ev-report-view [aria-label="Remove Long terms"]'));
    await until(() => expect(page().querySelector('.ev-report-view')).toBeNull());
  });
});
