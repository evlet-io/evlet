// 0.4.22: report drill-down links and units from the filters, remembered values on a new record, and the CSV
// import's header reading and mapping guess. Server answers are the recorded fixtures with the new keys added.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { csvHeader, guessMapping } from '../app/import-dialog';
import type { ImportInfo } from '../app/client';
import { adminRoutes, byText, click, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';

afterEach(() => stopAll());

const customer = (id: string, title: string) => ({ status: 200, etag: null, body: { id, title, rate: '0.00', credit_days: 30 } });

/** The recorded report schema with CustomerRates' first column drilling down to RatesByTerms and its rate total
 * taking its unit from the filters. */
function drillSchema() {
  const schema = copy(screen.report_schema);
  const report = schema.body.reports.find((item: any) => item.name === 'CustomerRates');
  report.columns[0] = { ...report.columns[0], report: 'RatesByTerms', filters: { min_days: 'credit_days', search: 'search' } };
  report.columns[3] = { ...report.columns[3], unit: null, unit_from_filters: true };
  return schema;
}

const reportRoutes = (run: any): Route[] => [
  ['GET', /\/api\/auth\/session$/, screen.report_session],
  ['GET', /\/api\/schema$/, drillSchema()],
  ['POST', /\/api\/_reports\/CustomerRates\/evaluate$/, screen.report_evaluate],
  ['POST', /\/api\/_reports\/CustomerRates$/, run],
  ['POST', /\/api\/_reports\/RatesByTerms\/evaluate$/, screen.report_evaluate],
  ['POST', /\/api\/_reports\/RatesByTerms$/, screen.report_grouped_run],
  ['GET', /\/api\/customer\/0000000000001$/, customer('0000000000001', 'Corniche Foods')],
  ['GET', /\/api\/customer\/0000000000002$/, customer('0000000000002', 'Al Noor Trading')],
  ['GET', /\/api\/customer\/0000000000004$/, customer('0000000000004', 'Oasis Water')],
];
const page = () => document.querySelector<HTMLElement>('.ev-report')!;

describe('report columns', () => {
  it('link each cell to another report with filters from the row and the run', async () => {
    const run = copy(screen.report_run);
    run.body.units = { rate: 'KWD' };
    start(reportRoutes(run), '/reports/CustomerRates?min_days=20&search=a');
    await until(() => expect(page().querySelectorAll('a.ev-report-drill')).toHaveLength(3));
    const link = page().querySelector<HTMLAnchorElement>('a.ev-report-drill')!;
    // credit_days comes from the row (45); search is no column, so it comes from the filters the report ran with.
    expect(link.getAttribute('href')).toBe('/reports/RatesByTerms?min_days=45&search=a');
    // The rate total takes the unit this run worked out.
    await until(() => expect(text(page().querySelector('tfoot'))).toContain('KWD'));
    click(link);
    await until(() => expect(location.pathname).toBe('/reports/RatesByTerms'));
  });

  it('leave out a total whose unit the run could not work out', async () => {
    const run = copy(screen.report_run);
    run.body.units = { rate: null };
    delete run.body.totals;
    start(reportRoutes(run), '/reports/CustomerRates?min_days=20');
    await until(() => expect(page().querySelectorAll('tbody tr:not(.spacer)').length).toBe(3));
    expect(text(page().querySelector('tfoot'))).not.toContain('AED');
  });
});

describe('remembered values', () => {
  it('fill a new record with what the person saved last', async () => {
    const schema = copy(screen.schema);
    const model = schema.body.models.find((item: any) => item.name === 'customer');
    model.fields.find((field: any) => field.name === 'credit_days').remember = true;
    const remembered = vi.fn(() => ({ status: 200, etag: null, body: { values: { credit_days: 60 } } }));
    const { host, sent } = start([['GET', /\/api\/auth\/session$/, adminRoutes()[0][2]], ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/customer\/remembered$/, remembered as any]], '/app/customer/new');
    await until(() => expect(host.querySelector<HTMLInputElement>('.fld[data-field="credit_days"] input')?.value).toBe('60'));
    expect(requests(sent, 'GET', /remembered$/)).toHaveLength(1);
  });

  it('are not asked for when no field remembers', async () => {
    const { host, sent } = start(adminRoutes(), '/app/customer/new');
    await until(() => expect(host.querySelector('.fld[data-field="title"] input')).not.toBeNull());
    expect(requests(sent, 'GET', /remembered$/)).toHaveLength(0);
  });
});

describe('CSV import', () => {
  const info: ImportInfo = {
    model: 'invoice', label: 'Invoices', limits: { characters: 2_000_000, rows: 5000, records: 1000 },
    columns: [{ path: 'customer', label: 'Customer', type: 'link', required: true }, { path: 'posted', label: 'Posted', type: 'date', required: true },
      { path: 'children.lines.item', label: 'Item', type: 'text', required: true, table: 'lines' }],
    saved: { mapping: { Date: 'posted' }, key: null },
  };

  it('reads the header row, quotes included', () => {
    expect(csvHeader('﻿"Customer, name",Date,"Say ""hi"""\r\nAcme,2026-03-01,x\r\n')).toEqual(['Customer, name', 'Date', 'Say "hi"']);
    expect(csvHeader('a,b')).toEqual(['a', 'b']);
  });

  it('starts from the saved mapping and fields with the same name', () => {
    expect(guessMapping(['customer', 'Date', 'Item', 'Other'], info)).toEqual({ customer: 'customer', Date: 'posted', Item: 'children.lines.item' });
  });

  it('opens from a list whose model is importable', async () => {
    const schema = copy(screen.schema);
    schema.body.models.find((item: any) => item.name === 'customer').importable = true;
    const { host } = start([['GET', /\/api\/auth\/session$/, adminRoutes()[0][2]], ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/customer\/import$/, { status: 200, etag: null, body: { ...info, model: 'customer' } }],
      ['GET', /\/api\/customer\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }]], '/app/customer');
    await until(() => expect(byText(host, 'button', 'Import')).not.toBeNull());
    click(byText(host, 'button', 'Import'));
    await until(() => expect(document.querySelector('.ev-import input[type="file"]')).not.toBeNull());
  });
});
