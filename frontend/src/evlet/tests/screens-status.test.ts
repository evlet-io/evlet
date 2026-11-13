// How a record's state and status show (State show=, Meta.status, Select colors), over the invoice
// responses recorded from the engine. The recorded invoice declares show="bar"; these tests change
// the schema the way another declaration would send it.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, lastToast, requests, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(stopAll);

const draft = invoice.invoice_draft.body.id;
const PAYMENT_STATUS = {
  name: 'payment_status', type: 'select', label: 'Payment status', choices: ['unpaid', 'partly_paid', 'paid'],
  colors: { unpaid: 'rose', partly_paid: 'amber', paid: 'green' }, read_only: true, required: false, hidden: false, stored: false,
  compute: null, default: null, help_text: '', placeholder: '', operators: [], applies_when: null, required_when: null, locked_when: null,
};

/** The recorded schema with the invoice changed: State show, Meta.status and a calculated payment status. */
function schema(source: Recorded, change: { show?: 'badge' | 'bar' | 'none' | null; status?: boolean }): Recorded {
  const result = copy(source);
  const model = result.body.models.find((item: any) => item.name === 'invoice');
  if (change.show === null) delete model.state.show; else if (change.show) model.state.show = change.show;
  if (change.status) {
    model.fields.push(PAYMENT_STATUS);
    model.status = 'payment_status';
    model.list_fields = [...model.list_fields, 'payment_status'];
  }
  return result;
}
const withStatus = (source: Recorded, value: string): Recorded => {
  const result = copy(source);
  if (Array.isArray(result.body.items)) for (const item of result.body.items) item.payment_status = value;
  else result.body.payment_status = value;
  return result;
};

const routes = (schemaBody: Recorded, detail: Recorded = invoice.invoice_draft, session: Recorded = invoice.session): Route[] => [
  ['GET', /\/api\/auth\/session$/, session],
  ['GET', /\/api\/schema$/, schemaBody],
  ['GET', new RegExp(`/api/invoice/${detail.body.id}$`), detail],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/journal/, invoice.invoice_draft_journal],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/attachments$/, invoice.invoice_attachments],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
];
const badges = (host: Element) => [...host.querySelectorAll<HTMLElement>('.doc-head .ev-status')];

describe('record status display', () => {
  it('shows a State as a header badge by default, with the moves this person may take as buttons and in its menu', async () => {
    const { host, sent } = start([...routes(schema(invoice.schema, { show: null })),
      ['POST', new RegExp(`/api/invoice/${draft}/moves/issue$`), invoice.invoice_issued]], `/app/invoice/${draft}`);
    await until(() => expect(badges(host)).toHaveLength(1));
    expect(host.querySelector('.stepper')).toBeNull();
    const badge = badges(host)[0];
    expect(text(badge)).toBe('Draft');
    expect(badge.tagName).toBe('BUTTON');
    expect(badge.getAttribute('aria-label')).toBe('Status: Draft. Change status');
    // The badge sits on the title's line, after the title.
    expect(badge.closest('.doc-title-line')).not.toBeNull();
    expect(badge.closest('.rec-meta')).toBeNull();
    // The moves stay as header buttons too.
    expect(byText(host, '.doc-head .ph-actions button', 'Issue')).not.toBeNull();
    click(badge);
    const items = [...document.querySelectorAll('[role="menuitem"]')].map(text);
    expect(items).toEqual(['Issue']);
    click(byText(document, '[role="menuitem"]', 'Issue'));
    await until(() => expect(requests(sent, 'POST', /moves\/issue$/)).toHaveLength(1));
    // Issued: Mark paid and Reopen are for admins, so the editor's badge offers only Void.
    await until(() => expect(text(badges(host)[0])).toBe('Issued'));
    expect(badges(host)[0].getAttribute('aria-label')).toBe('Status: Issued. Change status');
    click(badges(host)[0]);
    expect([...document.querySelectorAll('[role="menuitem"]')].map(text)).toEqual(['Void']);
  });

  it('shows and takes the moves of a record whose text id has letters, as every new id does', async () => {
    // The recordings number rows 1, 2, 3, so their ids are all digits; a time-ordered id is not.
    const textId = '01hff1w8vgqwf';
    const retext = (source: Recorded): Recorded => JSON.parse(JSON.stringify(source).split(draft).join(textId));
    const { host, sent } = start([...routes(schema(invoice.schema, { show: null }), retext(invoice.invoice_draft)),
      ['POST', new RegExp(`/api/invoice/${textId}/moves/issue$`), retext(invoice.invoice_issued)]], `/app/invoice/${textId}`);
    await until(() => expect(byText(host, '.doc-head .ph-actions button', 'Issue')).not.toBeNull());
    expect(badges(host)[0].tagName).toBe('BUTTON');
    click(byText(host, '.doc-head .ph-actions button', 'Issue'));
    await until(() => expect(requests(sent, 'POST', new RegExp(`/api/invoice/${textId}/moves/issue$`))).toHaveLength(1));
    await until(() => expect(text(badges(host)[0])).toBe('Issued'));
    expect(lastToast()).not.toMatch(/cannot move/);
  });

  it('keeps the badge plain for a role that may take no move, and for a guarded move that is not met', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: null }), invoice.invoice_draft_blocked), `/app/invoice/${draft}`);
    await until(() => expect(badges(host)).toHaveLength(1));
    const badge = badges(host)[0];
    expect(badge.tagName).toBe('SPAN');
    expect(badge.getAttribute('role')).toBe('img');
    expect(badge.getAttribute('aria-label')).toBe('Status: Draft');
    // The header button still explains why the move is blocked.
    click(byText(host, '.doc-head button', 'Issue'));
    expect(lastToast()).toBe('The condition for this move is not met');
  });

  it('shows neither badge nor bar for show="none"', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: 'none' })), `/app/invoice/${draft}`);
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    expect(host.querySelector('.stepper')).toBeNull();
    expect(badges(host)).toHaveLength(0);
  });

  it('draws the stage bar for show="bar": only a state this person can move to is a button', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: 'bar' })), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.stepper .step')).toHaveLength(4));
    expect(badges(host)).toHaveLength(0);
    const steps = [...host.querySelectorAll<HTMLElement>('.stepper .step')];
    expect(steps.map(step => step.querySelector('button') !== null)).toEqual([false, true, false, false]);
    // Paid and Void are reached by moves for other roles or by void: plain steps with no pointer.
    expect(steps.map(step => step.classList.contains('plain'))).toEqual([true, false, true, true]);
    expect(steps[1].querySelector('button')!.getAttribute('aria-label')).toBe('Issued. Move to Issued');
    expect(host.querySelector('.stepper')!.getAttribute('aria-label')).toBe('Invoice status');
  });

  it('puts the stage bar above the key cards and the white card, and a badge instead in a side panel', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: 'bar' })), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelector('.stepper')).not.toBeNull());
    const stepper = host.querySelector('.stepper')!;
    expect(stepper.compareDocumentPosition(host.querySelector('.ev-cmp-card')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    stopAll();
    const panelSchema = schema(invoice.schema, { show: 'bar' });
    panelSchema.body.models.find((item: any) => item.name === 'invoice').open_in = 'panel';
    const inPanel = start([...routes(panelSchema), ['GET', /\/api\/invoice\?/, invoice.invoices], ['GET', /\/api\/invoice\/(measures|aggregate)/, invoice.invoice_measures]],
      `/app/invoice?open=${draft}`);
    await until(() => expect(inPanel.host.querySelector('.ev-panel .doc-head .ev-status')).not.toBeNull());
    expect(inPanel.host.querySelector('.ev-panel .stepper')).toBeNull();
  });

  it('a guarded move that is not met leaves every step of the bar plain', async () => {
    const { host, sent } = start(routes(schema(invoice.schema, { show: 'bar' }), invoice.invoice_draft_blocked), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.stepper .step')).toHaveLength(4));
    expect(host.querySelectorAll('.stepper button')).toHaveLength(0);
    click(byText(host, '.stepper .step', 'Issued'));
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
  });

  it('shows Meta.status, a calculated Select, as a badge in its colour that never opens anything', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: 'bar', status: true }), withStatus(invoice.invoice_draft, 'partly_paid')), `/app/invoice/${draft}`);
    await until(() => expect(badges(host)).toHaveLength(1));
    const badge = badges(host)[0];
    expect(text(badge)).toBe('Partly paid');
    expect(badge.tagName).toBe('SPAN');
    expect(badge.className).toContain('b-amber');
    expect(badge.getAttribute('aria-label')).toBe('Payment status: Partly paid');
    // The pipeline keeps its bar; the badge is the payment status only.
    expect(host.querySelectorAll('.stepper .step')).toHaveLength(4);
    click(badge);
    expect(document.querySelector('[role="menuitem"]')).toBeNull();
  });

  it('shows a State badge and a Meta.status badge side by side', async () => {
    const { host } = start(routes(schema(invoice.schema, { show: 'badge', status: true }), withStatus(invoice.invoice_draft, 'unpaid')), `/app/invoice/${draft}`);
    await until(() => expect(badges(host)).toHaveLength(2));
    expect(badges(host).map(item => item.getAttribute('aria-label'))).toEqual(['Status: Draft. Change status', 'Payment status: Unpaid']);
    expect(badges(host)[1].className).toContain('b-rose');
  });

  it('fits the badge in the side panel header', async () => {
    const list: Route[] = [
      ...routes(schema(invoice.schema, { show: null })),
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
    ];
    const { host } = start(list, `/app/invoice?open=${draft}`);
    await until(() => expect(host.querySelector('.doc-panel .doc-head .ev-status')).not.toBeNull());
    expect(text(host.querySelector('.doc-panel .doc-head .ev-status'))).toBe('Draft');
    expect(host.querySelector('.doc-panel .stepper')).toBeNull();
  });
});

describe('list status display', () => {
  it('renders the State and Meta.status columns as named badges', async () => {
    const { host } = start([
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, schema(invoice.schema, { status: true })],
      ['GET', /\/api\/invoice\?/, withStatus(invoice.invoices, 'paid')],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
    ], '/app/invoice');
    const named = (prefix: string) => [...host.querySelectorAll<HTMLElement>('tbody .ev-status')].filter(item => item.getAttribute('aria-label')!.startsWith(prefix));
    await until(() => expect(named('Payment status: ')).toHaveLength(3));
    const paid = named('Payment status: ')[0];
    expect(text(paid)).toBe('Paid');
    expect(paid.className).toContain('b-green');
    expect(paid.getAttribute('aria-label')).toBe('Payment status: Paid');
    expect(named('Status: ').map(item => item.getAttribute('aria-label'))).toEqual(['Status: Draft', 'Status: Issued', 'Status: Void']);
    // In a list a badge is never a button.
    expect(host.querySelectorAll('tbody .ev-status-go')).toHaveLength(0);
  });
});
