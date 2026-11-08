// A list that opens its records in a side panel (Meta.open_in = "panel" or the Controls list view), over the invoice responses recorded from the engine: the draft (3) with two lines,
// the issued invoice (2) and the void one (1).
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, press, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(stopAll);

// Ids as the server sends them: 13-character text.
const [three, two, one] = [invoice.invoice_draft, invoice.invoice_revise_before, invoice.invoice_void].map(item => String(item.body.id));
const details: Record<string, Recorded> = { [three]: invoice.invoice_draft, [two]: invoice.invoice_revise_before, [one]: invoice.invoice_void };
/** The recorded schema, with the invoice opening records in `openIn` (absent: the recorded default). */
function schema(openIn?: 'page' | 'panel'): Recorded {
  const body = copy(invoice.schema).body;
  if (openIn) body.models.find((model: { name: string }) => model.name === 'invoice').open_in = openIn;
  return { ...invoice.schema, body };
}
const routes = (openIn: 'page' | 'panel' | undefined = 'panel'): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema(openIn)],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/\w+\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/\w+\/related\/invoice\.revises/, invoice.invoice_related_revisions],
  ['GET', /\/api\/invoice\/\w+\/journal/, invoice.invoice_journal],
  ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
  ['GET', /\/api\/invoice\/\w{13}$/, sent => copy(details[sent.url.match(/\/api\/invoice\/(\w{13})$/)![1]])],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ['GET', new RegExp(`/api/payment/${invoice.invoice_payment.body.id}$`), invoice.invoice_payment],
];

const row = (host: Element, id: string) => host.querySelector<HTMLElement>(`.ev-list tbody tr[data-key="${id}"]`);
const panel = (host: Element) => host.querySelector<HTMLElement>('.ev-panel');
const panelTitle = (host: Element) => text(panel(host)?.querySelector('.doc-head h1'));
const columns = (host: Element) => [...host.querySelectorAll('.ev-list thead th')].map(text);
const dialogTitle = () => text(document.querySelector('[role="dialog"] h2, [role="alertdialog"] h2, .modal h2'));

/** Storage holding the person's layout preferences, as Prefs reads them. */
function storage(values: Record<string, unknown>): Storage {
  const items = new Map(Object.entries(values).map(([key, value]) => [key, JSON.stringify(value)]));
  return { get length() { return items.size; }, clear: () => items.clear(), key: index => [...items.keys()][index] ?? null,
    getItem: key => items.get(key) ?? null, setItem: (key, value) => { items.set(key, value); }, removeItem: key => { items.delete(key); } };
}

/** Open the draft's Lines tab in the panel and change the first line's quantity: an unsaved change. */
async function editFirstLine(host: HTMLElement) {
  click(byText(panel(host)!, '[role="tab"]', /^Lines/));
  await until(() => expect(panel(host)!.querySelectorAll('.crow')).toHaveLength(2));
  click(panel(host)!.querySelector('.crow-h'));
  const input = () => panel(host)!.querySelector<HTMLInputElement>('.crow-ed input:not([type="checkbox"])');
  await until(() => expect(input()).not.toBeNull());
  typeInto(input()!, '7');
  await until(() => expect(panel(host)!.querySelector('.ev-savebar')).not.toBeNull());
}

describe('records in a side panel beside the list', () => {
  it('opens a row in the panel: the list narrows, the row is current and the address names it', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(row(host, three)).not.toBeNull());
    expect(columns(host)).toContain('Customer');
    click(row(host, three));
    await until(() => expect(location.search).toBe(`?open=${three}`));
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    // The list keeps the title, the State and the first amount, without bulk checkboxes.
    await until(() => expect(columns(host)).toEqual(['Number', 'Status', 'Total']));
    expect(row(host, three)!.getAttribute('aria-current')).toBe('true');
    expect(row(host, three)!.classList.contains('cur')).toBe(true);
    // Same sections as the full page: tabs, and the side column folded into Details.
    expect([...panel(host)!.querySelectorAll('.doc-tabs [role="tab"]')].map(text)).toEqual(['Activity', 'Details', 'Lines2', 'Related', 'Postings']);
    expect(panel(host)!.querySelector('.doc-side')).toBeNull();
    click(byText(panel(host)!, '[role="tab"]', 'Details'));
    await until(() => expect(byText(panel(host)!, '.side-sec h4', /^Record info/)).not.toBeNull());
    expect(byText(panel(host)!, '.side-sec h4', /^Key fields/)).toBeNull();
    // The draft's files in the panel offer the same remove as the full page.
    await until(() => expect(panel(host)!.querySelector('.ev-file-remove')).not.toBeNull());
    expect(panel(host)!.getAttribute('aria-label')).toBe('Invoice Draft invoice');
    expect(text(host.querySelector('.ev-list-split .ev-sr'))).toBe('Opened Invoice Draft invoice');
    // The divider is a separator a keyboard can move.
    const divider = host.querySelector<HTMLElement>('.split-div')!;
    expect(divider.getAttribute('role')).toBe('separator');
    expect(divider.getAttribute('aria-valuenow')).toBe('480');
  });

  it('follows the arrow keys along the list, replacing the history entry, and Back closes the panel', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(row(host, three)).not.toBeNull());
    const before = history.length;
    click(row(host, three));
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    expect(history.length).toBe(before + 1);
    press(row(host, three)!, 'ArrowDown');
    await until(() => expect(location.search).toBe(`?open=${two}`));
    await until(() => expect(panelTitle(host)).toBe('INV-2026-00002'));
    expect(row(host, two)!.getAttribute('aria-current')).toBe('true');
    expect(document.activeElement).toBe(row(host, two));
    press(row(host, two)!, 'ArrowDown');
    await until(() => expect(location.search).toBe(`?open=${one}`));
    expect(history.length).toBe(before + 1);
    history.back();
    await until(() => expect(location.search).toBe(''));
    await until(() => expect(panel(host)).toBeNull());
  });

  it('switching tabs keeps the record and writes rtab; leaving the record with unsaved changes asks', async () => {
    const { host } = start(routes(), `/app/invoice?open=${three}`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    await editFirstLine(host);
    await until(() => expect(location.search).toBe(`?open=${three}&rtab=t-lines`));
    // Another tab of the same record does not ask.
    click(byText(panel(host)!, '[role="tab"]', 'Activity'));
    await until(() => expect(location.search).toBe(`?open=${three}`));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    // Another row does: Cancel keeps the record and its change, Discard moves on.
    press(row(host, three)!, 'ArrowDown');
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(document, '[role="dialog"] button', 'Cancel'));
    await until(() => expect(location.search).toBe(`?open=${three}`));
    expect(panelTitle(host)).toBe('Draft invoice');
    expect(panel(host)!.querySelector('.ev-savebar')).not.toBeNull();
    click(row(host, two));
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(document, '[role="dialog"] button', 'Discard changes'));
    await until(() => expect(panelTitle(host)).toBe('INV-2026-00002'));
    expect(location.search).toBe(`?open=${two}`);
  });

  it('a link or a reload opens the same record and tab; Escape closes it and returns focus to the row', async () => {
    const { host } = start(routes('page'), `/app/invoice?open=${three}&rtab=t-lines`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    expect(byText(panel(host)!, '[role="tab"]', /^Lines/)!.getAttribute('aria-selected')).toBe('true');
    // Narrow child rows: the first value, the amount and Open full table.
    await until(() => expect(panel(host)!.querySelectorAll('.crow')).toHaveLength(2));
    expect(byText(panel(host)!, '.crows button', 'Open full table')).not.toBeNull();
    press(panel(host)!.querySelector('.crow-h')!, 'Escape');
    await until(() => expect(location.search).toBe(''));
    expect(panel(host)).toBeNull();
    await until(() => expect(document.activeElement).toBe(row(host, three)));
  });

  it('opens on the full page when the model says so; an old per-person choice is ignored and there is no menu to change it', async () => {
    const user = invoice.session.body.user.username;
    const { host } = start(routes('page'), '/app/invoice', { storage: storage({ [`evlet.${user}.openIn.invoice`]: 'panel' }) });
    await until(() => expect(row(host, three)).not.toBeNull());
    expect(host.querySelector('.ev-list button[aria-label="Open records in"]')).toBeNull();
    click(row(host, three));
    await until(() => expect(location.pathname).toBe(`/app/invoice/${three}`));
  });

  it('never shows the filter panel and the record panel together, and brings the filters back when the record closes', async () => {
    const { host } = start(routes(), `/app/invoice?f=${encodeURIComponent(JSON.stringify([["total", "gt", "0"]]))}`);
    await until(() => expect(row(host, three)).not.toBeNull());
    const filterButton = byText<HTMLButtonElement>(host, '.ph-actions button', /^Filter/)!;
    const dock = () => host.querySelector('.ev-filter-dock .fb-dock');
    expect(filterButton.classList.contains('has-filters')).toBe(true);
    click(filterButton);
    await until(() => expect(dock()).not.toBeNull());
    expect(filterButton.getAttribute('aria-expanded')).toBe('true');
    // A row opens the record and closes the filters; they stay applied and counted.
    click(row(host, three));
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    expect(dock()).toBeNull();
    expect(filterButton.getAttribute('aria-expanded')).toBe('false');
    expect(filterButton.classList.contains('has-filters')).toBe(true);
    expect(new URLSearchParams(location.search).get('open')).toBe(three);
    expect(new URLSearchParams(location.search).get('f')).not.toBeNull();
    // Back closes the record and the filter panel comes back, without taking focus.
    history.back();
    await until(() => expect(panel(host)).toBeNull());
    await until(() => expect(dock()).not.toBeNull());
    await Promise.resolve();
    expect(dock()!.contains(document.activeElement)).toBe(false);
    expect(new URLSearchParams(location.search).get('open')).toBeNull();
    expect(new URLSearchParams(location.search).get('f')).not.toBeNull();
    // Forward opens the record again and closes the filters.
    history.forward();
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    expect(dock()).toBeNull();
    // Filter closes the record and opens the filters; closing the filters leaves both closed.
    click(filterButton);
    await until(() => expect(dock()).not.toBeNull());
    expect(panel(host)).toBeNull();
    expect(new URLSearchParams(location.search).get('open')).toBeNull();
    click(filterButton);
    await until(() => expect(dock()).toBeNull());
    // A record opened without the filters open closes to the list alone.
    click(row(host, two));
    await until(() => expect(panelTitle(host)).toBe('INV-2026-00002'));
    press(panel(host)!, 'Escape');
    await until(() => expect(panel(host)).toBeNull());
    expect(dock()).toBeNull();
  });

  it('Filter asks before closing a record with unsaved changes; Cancel keeps the record, Discard opens the filters', async () => {
    const { host } = start(routes(), `/app/invoice?open=${three}`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    await editFirstLine(host);
    const filterButton = byText<HTMLButtonElement>(host, '.ph-actions button', /^Filter/)!;
    const dock = () => host.querySelector('.ev-filter-dock .fb-dock');
    click(filterButton);
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(document, '[role="dialog"] button', 'Cancel'));
    await until(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(panelTitle(host)).toBe('Draft invoice');
    expect(panel(host)!.querySelector('.ev-savebar')).not.toBeNull();
    expect(dock()).toBeNull();
    expect(location.search).toContain(`open=${three}`);
    click(filterButton);
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    click(byText(document, '[role="dialog"] button', 'Discard changes'));
    await until(() => expect(dock()).not.toBeNull());
    expect(panel(host)).toBeNull();
    expect(new URLSearchParams(location.search).get('open')).toBeNull();
  });

  it('expands to the full page at the same tab with the unsaved change, without asking', async () => {
    const { host } = start(routes(), `/app/invoice?open=${three}`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    await editFirstLine(host);
    click(panel(host)!.querySelector('.ev-panel-bar button[aria-label="Open full page"]'));
    await until(() => expect(location.pathname + location.search).toBe(`/app/invoice/${three}?tab=t-lines`));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await until(() => expect(host.querySelector('.ev-savebar')).not.toBeNull());
    expect(host.querySelector('.ev-panel')).toBeNull();
    expect(host.querySelector('.doc-side')).not.toBeNull();
  });

  it('keeps two header buttons in the panel and moves the rest into More', async () => {
    const { host } = start(routes(), `/app/invoice?open=${three}`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    const buttons = () => [...panel(host)!.querySelectorAll('.doc-head .ph-actions button:not([aria-label="More actions"])')].map(text);
    await until(() => expect(buttons()).toEqual(['Issue', 'Record payment']));
    click(panel(host)!.querySelector('button[aria-label="More actions"]'));
    const items = () => [...document.querySelectorAll('[role="menuitem"]')].map(text);
    await until(() => expect(items()).toContain('Open full page'));
    expect(items()).toContain('Copy link');
  });

  it('offers Print in More, not as a header button, when the role may print', async () => {
    const printable = copy(schema('panel'));
    printable.body.printing = { available: true };
    printable.body.models.find((model: { name: string }) => model.name === 'invoice').prints = [{ name: 'InvoicePrint', title: 'Invoice', default: true }];
    const { host } = start(routes().map(route => String(route[1]).includes('schema') ? [route[0], route[1], printable] as Route : route), `/app/invoice?open=${three}`);
    await until(() => expect(panelTitle(host)).toBe('Draft invoice'));
    const buttons = () => [...panel(host)!.querySelectorAll('.doc-head .ph-actions button:not([aria-label="More actions"])')].map(text);
    await until(() => expect(buttons()).toEqual(['Issue', 'Record payment']));
    click(panel(host)!.querySelector('button[aria-label="More actions"]'));
    const items = () => [...document.querySelectorAll('[role="menuitem"]')].map(text);
    await until(() => expect(items()).toContain('Print'));
  });

  it('creates a record in the panel: New opens ?open=new and Cancel closes it', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(row(host, three)).not.toBeNull());
    click(byText(host, '.ev-list .ph-actions button, .ev-list button', /New invoice/));
    await until(() => expect(location.search).toBe('?open=new'));
    await until(() => expect(panelTitle(host)).toBe('New invoice'));
    click(byText(panel(host)!, '.ev-savebar button', 'Cancel'));
    await until(() => expect(location.search).toBe(''));
    expect(panel(host)).toBeNull();
  });
});
