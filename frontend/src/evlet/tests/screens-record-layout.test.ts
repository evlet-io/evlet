// A declared record layout (Meta.layout with evlet.Main holding a Table, then Html, Chart and Related blocks), over the
// responses recorded for the Site model (layout_* in screens-fixture.json): the blocks are "more information" rows
// after the primary area, related jobs come from the list route and charts from the aggregate route with the
// block's filters, and Html shows record values only as text.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded, type Sent } from './server';
import { blockFilters, viewAllHref } from '../app/record-blocks';

afterEach(() => { vi.unstubAllGlobals(); stopAll(); });

const site = screen.layout_site_detail.body.id as string;

function schema(change?: (model: any) => void): Recorded {
  const body = copy(screen.layout_schema.body);
  const model = body.models.find((item: { name: string }) => item.name === 'site');
  change?.(model);
  return { ...screen.layout_schema, body };
}
const filtersOf = (sent: Sent) => JSON.parse(new URL(sent.url, 'http://127.0.0.1').searchParams.get('filters') ?? 'null');
const jobs = (sent: Sent) => {
  const filters = filtersOf(sent);
  return filters.some((item: unknown[]) => item[0] === 'status') ? screen.layout_open_jobs : screen.layout_same_region;
};
const routes = (model?: (model: any) => void): Route[] => [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, schema(model)],
  ['GET', /\/api\/job\/aggregate\?/, (sent: Sent) => new URL(sent.url, 'http://127.0.0.1').searchParams.get('group_by') ? screen.layout_by_status : screen.layout_totals],
  ['GET', /\/api\/job\?/, jobs],
  ['GET', new RegExp(`/api/site/${site}$`), screen.layout_site_detail],
  ['GET', /\/api\/site\?/, { status: 200, etag: null, body: { items: [screen.layout_site_detail.body], total: 1, limit: 50, offset: 0 } }],
];

const rows = (host: Element) => [...host.querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')));

async function open(path = `/app/site/${site}`, model?: (model: any) => void) {
  const started = start(routes(model), path);
  await until(() => expect(text(started.host.querySelector('.doc-head h1'))).toContain('Dock'));
  return started;
}

describe('a declared record layout', () => {
  it('shows its blocks in order as more information after the primary area, and no automatic Related', async () => {
    const { host } = await open();
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    // The primary area: Main's fields, then its Table.
    expect(host.querySelector('.ev-cmp-main [data-field="region"]')).not.toBeNull();
    expect(text(host.querySelector('.ev-cmp-main .ev-cmp-th h3'))).toBe('Visits');
    // The blocks in the layout's order, then the fields no section lists (Other); listing related records itself turns
    // the automatic Related row off.
    expect(rows(host)).toEqual(['Notes', 'Totals', 'Hours by status', 'Open jobs', 'Same region', 'Other']);
    expect(host.querySelector('.ev-more-row[data-section="Other"] [data-field="notes"]')).not.toBeNull();
  });

  it('shows Html with record values as text, never as markup', async () => {
    const { host } = await open();
    const block = host.querySelector('.ev-html')!;
    await until(() => expect(text(block)).toBe('Work at Dock <script>alert(1)</script> & Co in North. Write {braces} as they are.'));
    expect(block.querySelector('script')).toBeNull();
    expect(block.querySelector('strong')?.textContent).toBe('Dock <script>alert(1)</script> & Co');
  });

  it('charts declared measures through the aggregate route, filtered to this record', async () => {
    const { host, sent } = await open();
    await until(() => expect(text(host.querySelector('.ev-chart-stat'))).toContain('Jobs'));
    const asked = requests(sent, 'GET', /\/api\/job\/aggregate/);
    expect(asked.map(filtersOf)).toEqual([[['site', 'eq', site]], [['site', 'eq', site]]]);
    expect(asked.map(item => new URL(item.url, 'http://127.0.0.1').searchParams.get('measures'))).toEqual(['hours,jobs', 'hours']);
    // The stat numbers are the aggregate route's.
    expect([...host.querySelectorAll('.ev-chart-stat .stat-card')].map(stat => `${text(stat.querySelector('span'))} ${text(stat.querySelector('b'))}`)).toEqual(['Hours 25.0000', 'Jobs 4']);
    // The bar chart draws one bar per status, named for screen readers.
    const bars = host.querySelector('.ev-chart-bar svg[role="img"]')!;
    expect(bars.getAttribute('aria-label')).toBe('Hours by status chart');
    expect([...bars.querySelectorAll('rect title')].map(node => node.textContent)).toEqual(['Done: Hours 12.0000', 'Open: Hours 13.0000']);
  });

  it('lists related records through the list route with the block filters, a Ref filled from the record', async () => {
    const { host, sent } = await open();
    await until(() => expect([...host.querySelectorAll('.ev-related[aria-label="Open jobs"] .ev-row-link')].map(text)).toEqual(['Design', 'Survey']));
    const asked = requests(sent, 'GET', /\/api\/job\?/);
    expect(asked.map(filtersOf)).toEqual(expect.arrayContaining([
      [['site', 'eq', site], ['status', 'ne', 'done']],
      [['site', 'eq', site], ['region', 'eq', 'north']],
    ]));
    const openJobs = asked.find(item => filtersOf(item).length === 2 && filtersOf(item)[1][0] === 'status')!;
    expect(new URL(openJobs.url, 'http://127.0.0.1').searchParams.get('order_by')).toBe('-hours');
    expect(new URL(openJobs.url, 'http://127.0.0.1').searchParams.get('limit')).toBe('2');
    // Three open jobs at two a page: a pager, and View all opens the same list.
    const block = host.querySelector('.ev-related[aria-label="Open jobs"]')!;
    expect(text(block.querySelector('.ev-pager'))).toContain('1–2 of 3');
    const all = block.querySelector<HTMLAnchorElement>('.ev-view-all')!;
    expect(JSON.parse(new URL(all.href).searchParams.get('f')!)).toEqual([['site', 'eq', site], ['status', 'nin', ['done']]]);
    // The Table shows in the primary area, with its row.
    expect(text(host.querySelector('.ev-cmp-main [data-table-block="visits"]'))).toContain('Survey');
  });

  it('brings the automatic Related row back when Meta.side_blocks names it', async () => {
    const { host } = await open(`/app/site/${site}`, model => { model.side_blocks = ['related']; });
    expect(rows(host)).toEqual(['Notes', 'Totals', 'Hours by status', 'Open jobs', 'Same region', 'Other', 'Related']);
  });

  it('shows the same blocks in a side panel', async () => {
    const { host } = await open(`/app/site?open=${site}`, model => { model.open_in = 'panel'; });
    const panel = host.querySelector<HTMLElement>('.ev-panel')!;
    expect(rows(panel)).toEqual(['Notes', 'Totals', 'Hours by status', 'Open jobs', 'Same region', 'Other']);
    await until(() => expect(panel.querySelector('.ev-html')).not.toBeNull());
    await until(() => expect(panel.querySelector('.ev-related[aria-label="Same region"]')).not.toBeNull());
  });
});

describe('record layout filters', () => {
  const block = { link: 'site', filters: [['region', 'eq', { $expr: 'ref', args: ['region'] }], ['hours', 'gt', { $expr: 'ref', args: ['limit'] }]] as Array<[string, string, unknown]> };

  it('fills Ref values from the saved record, and matches nothing when an ordered compare has no value', () => {
    expect(blockFilters(block, { id: 'a1', saved: name => ({ region: 'north', limit: '2' })[name] })).toEqual([['site', 'eq', 'a1'], ['region', 'eq', 'north'], ['hours', 'gt', '2']]);
    expect(blockFilters({ ...block, filters: block.filters.slice(0, 1) }, { id: 'a1', saved: () => null })).toEqual([['site', 'eq', 'a1'], ['region', 'eq', null]]);
    expect(blockFilters(block, { id: 'a1', saved: name => name === 'region' ? 'north' : null })).toBeNull();
  });

  it('offers View all only when the list filters can hold every condition', async () => {
    const ctx = { schema: { schema: schema().body, model: (name: string) => schema().body.models.find((item: any) => item.name === name) },
      router: { href: (_: string, params: { model: string }, query: { f: string }) => `/app/${params.model}?f=${query.f}` } } as any;
    const job = ctx.schema.model('job');
    expect(viewAllHref(ctx, job, [['site', 'eq', site], ['status', 'eq', 'open']])).toBe(`/app/job?f=${JSON.stringify([['site', 'eq', site], ['status', 'in', ['open']]])}`);
    expect(viewAllHref(ctx, job, [['site', 'eq', site], ['nope', 'eq', 1]])).toBeNull();
  });
});

describe('a new record with a declared layout', () => {
  const focusedField = () => document.activeElement?.closest('[data-field]')?.getAttribute('data-field') ?? null;

  it('focuses the first field of the primary area, even when the layout lists another section first', async () => {
    const otherFirst = (model: any) => {
      const blocks = model.record_layout.blocks;
      model.record_layout.blocks = [blocks.find((block: { label?: string }) => block.label === 'Other'), ...blocks.filter((block: { label?: string }) => block.label !== 'Other')];
    };
    const { host } = start(routes(otherFirst), '/app/site/new');
    await until(() => expect(host.querySelector('[data-field="notes"]')).not.toBeNull());
    await until(() => expect(focusedField()).toBe('title'));
  });

  it('keeps the form order in the primary area', async () => {
    const { host } = start(routes(), '/app/site/new');
    await until(() => expect(host.querySelector('[data-field="title"]')).not.toBeNull());
    await until(() => expect(focusedField()).toBe('title'));
  });
});
