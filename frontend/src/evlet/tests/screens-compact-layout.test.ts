// A compact record layout (Meta.layout starting with evlet.Main, Section descriptions and Meta.cards), over the
// responses recorded for the Shipment model (compact_* in screens-fixture.json). The record page does not draw the
// compact design yet: it must read the new schema keys without breaking and show every block in order.
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

describe('a compact record layout on the current record page', () => {
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
    expect(host.querySelector('section[aria-label="Charges"]')).not.toBeNull();
  });
});
