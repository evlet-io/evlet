// A compact record layout (Meta.layout starting with evlet.Main, Section descriptions and Meta.cards), over the
// responses recorded for the Shipment model (compact_* in screens-fixture.json): the record page draws cards, the Main
// card without a heading, "more information" rows with a one-line summary while closed, and no tabs.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { recorded, screen, start, stopAll, text, until, type Route } from './screens-helpers';

afterEach(() => { vi.unstubAllGlobals(); stopAll(); });

const shipment = screen.compact_shipment_detail.body.id as string;
const routes: Route[] = [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, screen.compact_schema],
  ['GET', new RegExp(`/api/shipment/${shipment}/measures$`), screen.compact_measures],
  ['GET', new RegExp(`/api/shipment/${shipment}$`), screen.compact_shipment_detail],
  ['GET', /\/api\/parcel\?/, screen.compact_parcels],
  ['GET', /\/api\/shipment\?/, { status: 200, etag: null, body: { items: [screen.compact_shipment_detail.body], total: 1, limit: 50, offset: 0 } }],
];

describe('a compact record page', () => {
  it('sends cards, the Main area and section descriptions in the schema', () => {
    const model = screen.compact_schema.body.models.find((item: { name: string }) => item.name === 'shipment');
    expect(model.cards).toEqual(['total', 'parcels', 'weight']);
    expect(model.record_layout.compact).toBe(true);
    expect(model.record_layout.blocks.filter((block: { main?: boolean }) => block.main)).toEqual([
      { kind: 'section', label: 'Shipment', main: true }, { kind: 'table', name: 'stops', main: true }]);
    expect(model.layout.find((group: { label: string }) => group.label === 'Charges').description).toBe('What the shipment is worth and what it costs.');
  });

  it('opens the record and shows its fields, table and sections in order', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toContain('Spring order'));
    await until(() => expect(host.querySelector('[data-field="carrier"]')).not.toBeNull());
    expect(host.querySelector('[data-field="declared"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Charges"]')).not.toBeNull();
  });

  it('shows Meta.cards at the top instead of the summary strip, measures included', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelectorAll('.ev-cards .stat-card')).toHaveLength(3));
    const cards = [...host.querySelectorAll('.ev-cards .stat-card')].map(card => text(card));
    expect(cards[0]).toContain('Total');
    expect(cards[0]).toContain('1,235.50');
    await until(() => expect(text(host.querySelectorAll('.ev-cards .stat-card')[1])).toContain('2'));
    expect(text(host.querySelectorAll('.ev-cards .stat-card')[2])).toContain('11.5');
  });

  it('has no tabs, keeps the header to the title and badges, and puts key fields in Record info', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('.ev-cmp-main')).not.toBeNull());
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(host.querySelector('.doc-head .rec-meta')).toBeNull();
    // The Main fields have no heading; the state of the record is not a tab or section title.
    expect(host.querySelector('.ev-cmp-main h3:not(.ev-cmp-th h3)')).toBeNull();
    const info = [...host.querySelectorAll('.side-sec')].find(block => text(block.querySelector('h4')) === 'Record info');
    expect(info?.querySelector('[data-field="carrier"]')).not.toBeNull();
  });

  it('shows a Main table compactly with its row count and Add, and lists the rest as more information', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('.ev-cmp-th')).not.toBeNull());
    expect(text(host.querySelector('.ev-cmp-th h3'))).toBe('Stops');
    expect(text(host.querySelector('.ev-cmp-th .ev-cmp-count'))).toBe('1');
    expect(text(host.querySelector('.ev-cmp-th .ev-cmp-add'))).toBe('Add stop');
    expect(text(host.querySelector('.ev-more-label'))).toBe('More information');
    const rows = [...host.querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')));
    expect(rows).toEqual(['Charges', 'Notes', 'Parcels']);
    expect(text(host.querySelector('[data-section="Charges"] .ev-more-desc'))).toBe('What the shipment is worth and what it costs.');
  });

  it('summarises a closed section on one line', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('[data-section="Charges"]')).not.toBeNull());
    const charges = host.querySelector('[data-section="Charges"]')!;
    // Notes is empty and starts closed (collapsed='empty'): one muted line says so.
    expect(host.querySelector('[data-section="Notes"]')!.classList.contains('closed')).toBe(true);
    expect(text(host.querySelector('[data-section="Notes"] .ev-more-sum'))).toBe('Nothing filled in');
    expect(charges.classList.contains('closed')).toBe(false);
    (charges.querySelector('.ev-more-toggle') as HTMLButtonElement).click();
    await until(() => expect(charges.classList.contains('closed')).toBe(true));
    const summary = charges.querySelector('.ev-more-sum') as HTMLElement;
    expect(summary.hidden).toBe(false);
    expect(text(summary)).toMatch(/^Declared value: 1,200(\.0+)? · Fee: 35\.5\d* · Total: 1,235\.5\d*$/);
    expect(summary.title).toBe(text(summary));
    expect(charges.querySelector('.ev-more-toggle')!.getAttribute('aria-expanded')).toBe('false');
    // The fields stay in the page while closed, so a #field address or an error can still open the row.
    expect(charges.querySelector('[data-field="declared"]')).not.toBeNull();
  });

  it('counts the rows of a closed Table row', async () => {
    const { rowsText, rowNoun } = await import('../app/record-compact');
    expect(rowsText(0)).toBe('No rows');
    expect(rowsText(1)).toBe('1 row');
    expect(rowsText(14)).toBe('14 rows');
    expect(rowNoun('Milestones')).toBe('milestone');
    expect(rowNoun('Address')).toBe('row');
  });
});
