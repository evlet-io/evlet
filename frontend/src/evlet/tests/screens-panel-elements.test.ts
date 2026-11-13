// What a record shows when a list opens it in its side panel (model.panel: panel=False flags, Meta.panel or the
// Controls toggles), over the recorded invoice responses. The full page ignores it.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';
import type { PanelSchema } from '../model/schema';

afterEach(stopAll);

const three = String(invoice.invoice_draft.body.id);

function schema(panel?: PanelSchema): Recorded {
  const body = copy(invoice.schema).body;
  const model = body.models.find((item: { name: string }) => item.name === 'invoice');
  model.open_in = 'panel';
  if (panel) model.panel = panel;
  return { ...invoice.schema, body };
}
const routes = (panel?: PanelSchema): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema(panel)],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/\w+\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/\w+\/journal/, invoice.invoice_journal],
  ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
  ['GET', new RegExp(`/api/invoice/${three}$`), invoice.invoice_draft],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ['GET', new RegExp(`/api/payment/${invoice.invoice_payment.body.id}$`), invoice.invoice_payment],
];

const panel = (host: Element) => host.querySelector<HTMLElement>('.ev-panel')!;
/** The record's parts besides its sections: the primary area's tables, then the "more information" rows that are not sections. */
const rows = (root: Element) => [...root.querySelectorAll('.ev-more-row')].map(node => node.getAttribute('data-section'));
const mainTables = (root: Element) => [...root.querySelectorAll('.ev-cmp-main .ev-cmp-th h3')].map(text);
/** The layout's sections shown: Invoice (the primary area) when it has fields, and Amounts as a row. */
const sections = (root: Element) => [...(root.querySelector('.ev-cmp-main [data-field]') ? ['Invoice'] : []), ...rows(root).filter(title => title === 'Amounts')];
const blocks = (root: Element) => [...root.querySelectorAll('.side-sec h4')].map(node => text(node));
const field = (root: Element, name: string) => root.querySelector(`[data-field="${name}"]`);

async function openDraft(panelSchema?: PanelSchema) {
  const started = start(routes(panelSchema), `/app/invoice?open=${three}`);
  await until(() => expect(text(panel(started.host)?.querySelector('.doc-head h1'))).toBe('Draft invoice'));
  return started.host;
}

describe('what a record shows in a side panel', () => {
  it('shows everything without model.panel, and no full page note', async () => {
    const host = await openDraft();
    await until(() => expect(mainTables(panel(host))).toEqual(['Lines']));
    expect(rows(panel(host))).toEqual(['Amounts', 'Related', 'Postings']);
    expect(sections(panel(host))).toEqual(['Invoice', 'Amounts']);
    await until(() => expect(blocks(panel(host))).toEqual(['Attachments', 'Activity']));
    expect(panel(host).querySelector('.ev-panel-more')).toBeNull();
  });

  it('leaves out hidden sections, fields, parts, blocks and the summary, and offers the full page', async () => {
    const host = await openDraft({ sections: ['Amounts'], fields: ['due_on'], tabs: ['activity', 'lines', 'postings'], blocks: ['info', 'summary'] });
    await until(() => expect(rows(panel(host))).toEqual(['Related']));
    expect(mainTables(panel(host))).toEqual([]);
    expect(panel(host).querySelector('.sum')).toBeNull();
    expect(sections(panel(host))).toEqual(['Invoice']);
    expect(field(panel(host), 'customer')).not.toBeNull();
    expect(field(panel(host), 'due_on')).toBeNull();
    expect(field(panel(host), 'total')).toBeNull();
    expect(blocks(panel(host))).not.toContain('Activity');
    expect(panel(host).querySelector('.ev-rec-foot')).toBeNull();
    const note = panel(host).querySelector('.ev-panel-more')!;
    expect(text(note)).toContain('Some parts of this invoice show only on the full page.');
    // The full page ignores model.panel.
    click(byText(note, 'button', 'Open full page'));
    await until(() => expect(location.pathname).toBe(`/app/invoice/${three}`));
    await until(() => expect(rows(host)).toEqual(['Amounts', 'Related', 'Postings']));
    expect(mainTables(host)).toEqual(['Lines']);
    expect(sections(host)).toEqual(['Invoice', 'Amounts']);
    expect(field(host, 'due_on')).not.toBeNull();
  });

  it('keeps only what Meta.panel names', async () => {
    // As view_for writes Meta.panel = ("lines", "customer", "related").
    const host = await openDraft({ fields: ['due_on', 'number', 'posting_date', 'status'], sections: ['Amounts'], tabs: ['activity', 'postings', 'revisions'],
      blocks: ['files', 'info', 'key_fields', 'summary'], order: ['lines', 'details', 'related'] });
    await until(() => expect(mainTables(panel(host))).toEqual(['Lines']));
    expect(rows(panel(host))).toEqual(['Related']);
    expect(sections(panel(host))).toEqual(['Invoice']);
    expect(field(panel(host), 'customer')).not.toBeNull();
    expect(field(panel(host), 'posting_date')).toBeNull();
    // The side column keeps only what Meta.panel leaves: no Attachments, Activity or Record info.
    expect(blocks(panel(host))).toEqual([]);
    expect(panel(host).querySelector('.ev-rec-foot')).toBeNull();
  });

  it('always shows a required field that is empty on a new record', async () => {
    const { host } = start(routes({ sections: ['Invoice', 'Amounts'], fields: ['customer', 'remarks'] }), '/app/invoice?open=new');
    await until(() => expect(text(panel(host)?.querySelector('.doc-head h1'))).toBe('New invoice'));
    // Customer is required and empty: it shows in its section although the panel hides both. Remarks stays hidden.
    await until(() => expect(field(panel(host), 'customer')).not.toBeNull());
    expect(sections(panel(host))).toEqual(['Invoice']);
    expect(field(panel(host), 'remarks')).toBeNull();
    expect(field(panel(host), 'due_on')).toBeNull();
  });
});
