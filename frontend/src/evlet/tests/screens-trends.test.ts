// Trend lines on a list's measure cards (docs/measures.md, Trends and colours): one aggregate per measure with a
// trend, grouped by its field and grain, with the list's filters and search; missing periods filled; color= as
// the card's accent; a failure only hides the line.
import { afterEach, describe, expect, it } from 'vitest';
import { invoice, requests, start, stopAll, until, type Route } from './screens-helpers';
import { copy, type Sent } from './server';
import { trendValues } from '../app/meta';

afterEach(stopAll);

describe('trendValues', () => {
  const sum = { name: 'total', function: 'sum' };
  it('takes the last periods up to the latest one, in date order, filling gaps with 0 for counts and sums', () => {
    const items = [{ d__month: '2026-01', total: '5.00' }, { d__month: '2026-03', total: '7.50' }, { d__month: null, total: '99' }, { d__month: '2025-11', total: '1' }];
    expect(trendValues(items, 'd__month', sum, { field: 'd', grain: 'month', points: 4 })).toEqual([0, 5, 0, 7.5]);
    expect(trendValues(items, 'd__month', sum, { field: 'd', grain: 'month', points: 6 })).toEqual([0, 1, 0, 5, 0, 7.5]);
  });
  it('steps back across years and days, and leaves gaps for a lowest or highest value', () => {
    expect(trendValues([{ y: '2026', n: 3 }, { y: '2024', n: 1 }], 'y', { name: 'n', function: 'count' }, { field: 'd', grain: 'year', points: 3 })).toEqual([1, 0, 3]);
    const days = trendValues([{ d: '2026-03-01', low: '2' }], 'd', { name: 'low', function: 'min' }, { field: 'd', grain: 'day', points: 3 })!;
    expect(days.map(value => Number.isNaN(value) ? 'gap' : value)).toEqual(['gap', 'gap', 2]);
    expect(trendValues([{ d: null, n: 1 }], 'd', { name: 'n', function: 'count' }, { field: 'd', grain: 'day', points: 3 })).toBeNull();
  });
});

describe('measure cards with a trend', () => {
  function routes(aggregate: (sent: Sent) => any): Route[] {
    const schema = copy(invoice.schema);
    const model = schema.body.models.find((entry: any) => entry.name === 'invoice');
    const outstanding = model.measures.find((measure: any) => measure.name === 'outstanding');
    Object.assign(outstanding, { trend: { field: 'posting_date', grain: 'month', points: 3 }, color: 'green' });
    return [
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, aggregate],
      ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
    ];
  }
  const query = (sent: Sent) => Object.fromEntries(new URL(sent.url, 'http://x').searchParams);

  it('draws the trend from the aggregate route with the list filters, in the measure colour', async () => {
    const { host, sent } = start(routes(() => ({ status: 200, etag: null, body: { group_by: ['posting_date__month'], measures: ['outstanding'],
      items: [{ posting_date__month: '2026-04', outstanding: '100.00' }, { posting_date__month: '2026-06', outstanding: '40.00' }] } })), '/app/invoice?tab=issued');
    await until(() => expect(host.querySelector('.stat-card .ev-spark')).not.toBeNull());
    const card = host.querySelector('.stat-card.has-ic')!;
    expect(card.className).toContain('tone-green');
    expect(card.querySelector('.ev-spark')!.getAttribute('aria-label')).toContain('3 points, from 100 to 40, low 0, high 100');
    const asked = requests(sent, 'GET', /invoice\/aggregate/).map(query).find(item => item.group_by === 'posting_date:month')!;
    expect(asked.measures).toBe('outstanding');
    expect(JSON.parse(asked.filters)).toEqual({ status: 'issued' });
    // The count has no trend: no line, and the next accent in turn.
    expect(host.querySelectorAll('.stat-card .ev-spark')).toHaveLength(1);
  });

  it('hides the line when the aggregate fails', async () => {
    const { host, sent } = start(routes(() => ({ status: 500, etag: null, body: { error: 'Broken' } })), '/app/invoice');
    await until(() => expect(requests(sent, 'GET', /invoice\/aggregate/).length).toBeGreaterThan(0));
    await until(() => expect(host.querySelector('.stat-card')).not.toBeNull());
    expect(host.querySelector('.stat-card .ev-spark')).toBeNull();
  });
});
