// A compact record layout (Meta.layout starting with evlet.Main, Section descriptions and Meta.cards), over the
// responses recorded for the Shipment model (compact_* in screens-fixture.json): the record page draws cards, the Main
// card without a heading, "more information" rows with a one-line summary while closed, and no tabs.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { byText, recorded, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

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

/** The recorded schema with the shipment changed: files on, opening in a side panel, a Meta.panel. */
function shipmentSchema(change: Record<string, unknown>): Recorded {
  const body = copy(screen.compact_schema).body;
  Object.assign(body.models.find((item: { name: string }) => item.name === 'shipment'), change);
  return { ...screen.compact_schema, body };
}
const withSchema = (schema: Recorded): Route[] => [routes[0], ['GET', /\/api\/schema$/, schema],
  ['GET', new RegExp(`/api/shipment/${shipment}/attachments$`), { status: 200, etag: null, body: [] }], ...routes.slice(2)];

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
    await until(() => expect(host.querySelectorAll('.ev-cards .ev-kc')).toHaveLength(3));
    const cards = [...host.querySelectorAll('.ev-cards .ev-kc')].map(card => text(card));
    expect(cards[0]).toContain('Total');
    expect(cards[0]).toContain('1,235.50');
    await until(() => expect(text(host.querySelectorAll('.ev-cards .ev-kc')[1])).toContain('2'));
    expect(text(host.querySelectorAll('.ev-cards .ev-kc')[2])).toContain('11.5');
  });

  it('has no tabs, keeps the header to the title and badges, and the side column to Attachments and Activity', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('.ev-cmp-main')).not.toBeNull());
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(host.querySelector('.doc-head .rec-meta')).toBeNull();
    // The Main fields have no heading; the state of the record is not a tab or section title.
    expect(host.querySelector('.ev-cmp-main h3:not(.ev-cmp-th h3)')).toBeNull();
    // No Record info block: key fields stay in the form (carrier is a Main field), the times are in the footer.
    const blocks = [...host.querySelectorAll('.doc-side .side-sec h4')].map(text);
    expect(blocks.every(title => ['Attachments', 'Activity'].includes(title))).toBe(true);
    expect(blocks).not.toContain('Record info');
    expect(host.querySelector('.doc-side [data-field]')).toBeNull();
    expect(host.querySelector('.ev-cmp-main [data-field="carrier"]')).not.toBeNull();
    // The footer row sits under the card: the ID with Copy, and Copy link.
    const footer = host.querySelector('.ev-cmp-card + .ev-rec-foot')!;
    expect(text(footer.querySelector('.ev-rf-id .mono'))).toBe(shipment);
    expect([...footer.querySelectorAll('button')].map(button => button.getAttribute('aria-label')).slice(-2)).toEqual(['Copy record ID', 'Copy link']);
  });

  it('ends the breadcrumb row with the side column drawer button, which opens and closes it', async () => {
    const { host } = await start(withSchema(shipmentSchema({ attachments: true })), `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('.ev-crumb-row .ev-side-toggle button')).not.toBeNull());
    const row = host.querySelector('.ev-crumb-row')!;
    expect(row.lastElementChild!.classList.contains('ev-side-toggle')).toBe(true);
    const button = row.querySelector<HTMLButtonElement>('.ev-side-toggle button')!;
    // The panel-right icon: the shell's sidebar toggle mirrored, for the column on the right.
    expect(button.innerHTML).toBe((await import('../ui/base')).Icon('panel-right', { size: 'sm' }).outerHTML);
    const aside = host.querySelector<HTMLElement>('.doc-side')!;
    expect(button.getAttribute('aria-controls')).toBe(aside.id);
    expect(button.getAttribute('aria-pressed')).toBe('false');
    const doc = host.querySelector('.doc')!;
    button.click();
    await until(() => expect(doc.classList.contains('side-open')).toBe(true));
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('.ev-side-drawer-scrim')!.classList.contains('show')).toBe(true);
    // Esc closes it, and so does a click on the scrim.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await until(() => expect(doc.classList.contains('side-open')).toBe(false));
    button.click();
    await until(() => expect(doc.classList.contains('side-open')).toBe(true));
    (host.querySelector('.ev-side-drawer-scrim') as HTMLElement).click();
    await until(() => expect(doc.classList.contains('side-open')).toBe(false));
  });

  it('shows a Main table compactly with its row count and Add, and lists the rest as more information', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('.ev-cmp-th')).not.toBeNull());
    expect(text(host.querySelector('.ev-cmp-th h3'))).toBe('Stops');
    expect(text(host.querySelector('.ev-cmp-th .ev-cmp-count'))).toBe('1');
    expect(text(host.querySelector('.ev-cmp-th .ev-cmp-add'))).toBe('Add stop');
    expect(text(host.querySelector('.ev-more-label'))).toBe('More information');
    // One card holds the key cards, the Main area and more information.
    const card = host.querySelector('.ev-cmp-card')!;
    expect(card.querySelector('.ev-cards')).not.toBeNull();
    expect(card.querySelector('.ev-cmp-main')).not.toBeNull();
    expect(card.querySelector('.ev-more')).not.toBeNull();
    const rows = [...host.querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')));
    expect(rows).toEqual(['Charges', 'Notes', 'Parcels']);
    expect(text(host.querySelector('[data-section="Charges"] .ev-more-desc'))).toBe('What the shipment is worth and what it costs.');
  });

  it('summarises a closed section on one line', async () => {
    const { host } = await start(routes, `/app/shipment/${shipment}`);
    await until(() => expect(host.querySelector('[data-section="Charges"]')).not.toBeNull());
    const charges = host.querySelector('[data-section="Charges"]')!;
    // Notes is empty and starts closed (collapsed='empty'): its one line lists the field as Not set.
    expect(host.querySelector('[data-section="Notes"]')!.classList.contains('closed')).toBe(true);
    expect(text(host.querySelector('[data-section="Notes"] .ev-more-sum'))).toBe('Notes: Not set');
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

// The list's side panel draws the same record view as the page: the same card, Main and more information, the same
// side column behind the same drawer button (at the end of the panel's top line), with Meta.panel hiding parts of it.
describe('a compact record in a side panel', () => {
  /** A ResizeObserver whose callbacks a test calls with each observed element's width. */
  function measured() {
    const observers: Array<{ callback: ResizeObserverCallback; targets: Element[] }> = [];
    vi.stubGlobal('ResizeObserver', class {
      entry: { callback: ResizeObserverCallback; targets: Element[] };
      constructor(callback: ResizeObserverCallback) { observers.push(this.entry = { callback, targets: [] }); }
      observe(target: Element) { this.entry.targets.push(target); }
      unobserve() {} disconnect() {}
    });
    return (target: Element, width: number) => {
      for (const item of observers) if (item.targets.includes(target)) item.callback([{ target, contentRect: { width } } as unknown as ResizeObserverEntry], {} as ResizeObserver);
    };
  }
  const panel = (host: Element) => host.querySelector<HTMLElement>('.ev-panel')!;

  it('renders the page\'s record view, with the side column behind the drawer button in a narrow panel', async () => {
    const resize = measured();
    const { host } = await start(withSchema(shipmentSchema({ attachments: true, open_in: 'panel' })), `/app/shipment?open=${shipment}`);
    await until(() => expect(panel(host)?.querySelector('.ev-cmp-card')).toBeTruthy());
    const doc = panel(host).querySelector<HTMLElement>('.doc')!;
    // The same view: the compact card, its cards, Main, more information and the footer row; no tabs.
    expect(doc.classList.contains('doc-compact')).toBe(true);
    expect(doc.classList.contains('doc-panel')).toBe(true);
    expect(panel(host).querySelector('[role="tablist"]')).toBeNull();
    expect(panel(host).querySelectorAll('.ev-cards .ev-kc')).toHaveLength(3);
    expect([...panel(host).querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')))).toEqual(['Charges', 'Notes', 'Parcels']);
    expect(panel(host).querySelector('.ev-cmp-card + .ev-rec-foot')).not.toBeNull();
    // The same side column, in the frame, with the drawer button at the end of the panel's top line.
    expect(panel(host).querySelector('.doc > .doc-side .side-sec h4') && text(panel(host).querySelector('.doc > .doc-side .side-sec h4'))).toBe('Attachments');
    const bar = panel(host).querySelector('.ev-panel-bar')!;
    const toggle = bar.querySelector<HTMLButtonElement>('.ev-side-toggle button')!;
    expect(toggle.getAttribute('aria-controls')).toBe(panel(host).querySelector('.doc-side')!.id);
    expect(panel(host).querySelector('.ev-crumb-row')).toBeNull();
    // Narrow or wide is the frame's own width, not the window's: a 480 px panel folds the side column into the drawer,
    // a wide one shows it beside the main column, as the page does.
    resize(doc, 480);
    resize(doc.querySelector('.doc-main')!, 444);
    expect([...doc.classList].filter(name => /^doc-(narrow|m|s|xs)$/.test(name))).toEqual(['doc-narrow', 'doc-m', 'doc-s']);
    toggle.click();
    await until(() => expect(doc.classList.contains('side-open')).toBe(true));
    // Escape closes the drawer first, not the panel.
    toggle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await until(() => expect(doc.classList.contains('side-open')).toBe(false));
    expect(panel(host)).not.toBeNull();
    resize(doc, 1000);
    resize(doc.querySelector('.doc-main')!, 590);
    expect([...doc.classList].filter(name => /^doc-(narrow|m|s|xs)$/.test(name))).toEqual(['doc-m']);
  });

  it('hides what Meta.panel leaves out of the shared view, and the full page shows it', async () => {
    const { host } = await start(withSchema(shipmentSchema({ attachments: true, open_in: 'panel', panel: { sections: ['Charges'], blocks: ['summary', 'files', 'info'] } })),
      `/app/shipment?open=${shipment}`);
    await until(() => expect(panel(host)?.querySelector('.ev-cmp-card')).toBeTruthy());
    expect(panel(host).querySelector('.ev-cards')).toBeNull();
    expect([...panel(host).querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')))).toEqual(['Notes', 'Parcels']);
    expect(panel(host).querySelector('[data-field="declared"]')).toBeNull();
    expect(panel(host).querySelector('.ev-rec-foot')).toBeNull();
    // Nothing left for the side column: no column and no drawer button.
    expect(panel(host).querySelector('.doc-side')).toBeNull();
    expect(panel(host).querySelector('.ev-side-toggle')).toBeNull();
    const note = panel(host).querySelector('.ev-panel-more')!;
    expect(text(note)).toContain('Some parts of this shipment show only on the full page.');
    byText(note, 'button', 'Open full page')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await until(() => expect(location.pathname).toBe(`/app/shipment/${shipment}`));
    await until(() => expect(host.querySelectorAll('.ev-cards .ev-kc')).toHaveLength(3));
    expect([...host.querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')))).toEqual(['Charges', 'Notes', 'Parcels']);
    expect(text(host.querySelector('.doc-side .side-sec h4'))).toBe('Attachments');
  });
});
