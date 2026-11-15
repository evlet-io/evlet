// The two zones of a record layout, over the responses recorded for the Crew model (zones_* in screens-fixture.json):
// every Main block drawn open in the order declared, without a toggle, whatever its place in Meta.layout; then "more
// information" rows (sections, a TableSection, Related and Other) that start closed. The row editor of the Hand rows
// follows the same rules.
import { afterEach, describe, expect, it } from 'vitest';
import { click, recorded, screen, start, stopAll, text, until, type Route } from './screens-helpers';

afterEach(stopAll);

const crew = screen.zones_crew_detail.body.id as string;
const routes: Route[] = [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, screen.zones_schema],
  ['GET', new RegExp(`/api/crew/${crew}$`), screen.zones_crew_detail],
  ['GET', /\/api\/crewvisit\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 5, offset: 0 } }],
];
const rows = (host: Element) => [...host.querySelectorAll('.ev-more-row')].map(row => [text(row.querySelector('.ev-more-toggle')), row.classList.contains('closed')]);
const row = (host: Element, title: string) => host.querySelector<HTMLElement>(`.ev-more-row[data-section="${title}"]`)!;

describe('a record layout with a main zone and more information', () => {
  it('draws every main open in order, without a toggle, then the rows of more information closed', async () => {
    const { host } = start(routes, `/app/crew/${crew}`);
    await until(() => expect(host.querySelector('.ev-cmp-main [data-field="site"]')).not.toBeNull());
    const main = host.querySelector('.ev-cmp-main')!;
    // Fields (the title is edited in the header), the Hands table, the People group under its heading, then the
    // untitled fields after it.
    expect([...main.querySelectorAll('.ff[data-field], [data-table-block], .ev-cmp-mh')].filter(node => !node.closest('[data-table-block]') || node.matches('[data-table-block]'))
      .map(node => node.getAttribute('data-field') ?? node.getAttribute('data-table-block') ?? text(node))).toEqual(['site', 'hands', 'People', 'lead', 'starts', 'ends']);
    expect(text(main.querySelector('[data-table-block="hands"] .ev-cmp-th h3'))).toBe('Hands');
    expect(main.querySelector('.ev-more-toggle, .fs-toggle')).toBeNull();
    // Sections, the Tools TableSection (no rows, collapsed='empty'), the Related block and Other: all closed.
    expect(rows(host)).toEqual([['Costs', true], ['Permit', true], ['Tools', true], ['Visits', true], ['Other', true]]);
    expect(row(host, 'Tools').querySelector('.ev-more-sum')).not.toBeNull();
    click(row(host, 'Tools').querySelector('.ev-more-toggle'));
    await until(() => expect(row(host, 'Tools').querySelector('[data-table-block="tools"]')).not.toBeNull());
  });

  it('opens a closed row that holds a required field on a new record', async () => {
    const { host } = start(routes, '/app/crew/new');
    await until(() => expect(row(host, 'Permit')).not.toBeNull());
    expect(row(host, 'Permit').classList.contains('closed')).toBe(false);
    expect(row(host, 'Costs').classList.contains('closed')).toBe(true);
    expect(host.querySelector('.ev-cmp-main [data-field="title"]')).not.toBeNull();
  });

  it('lays out the row editor the same way: the row model main open, its section closed', async () => {
    const { host } = start(routes, `/app/crew/${crew}`);
    await until(() => expect(host.querySelector('[data-table-block="hands"] tbody tr[data-key] .g-exp')).not.toBeNull());
    click(host.querySelector('[data-table-block="hands"] tbody tr[data-key] .g-exp'));
    await until(() => expect(host.querySelector('.g-ed-main .ev-row-form')).not.toBeNull());
    const form = host.querySelector('.g-ed-main .ev-row-form')!;
    expect(form.querySelector('.ev-cmp-grid [data-field="person"]')).not.toBeNull();
    const more = form.querySelector('.ev-more-row[data-section="More"]')!;
    expect(more.classList.contains('closed')).toBe(true);
    expect(text(more.querySelector('.ev-more-sum'))).toContain('Rigger');
  });
});
