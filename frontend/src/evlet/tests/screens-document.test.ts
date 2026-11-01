// The record view of a transaction document, over provisional fixtures that follow the documented
// JSON of later engine phases (see provisional/README.md): State and moves, Sequence, void and
// revise, effects, measures, meaning metadata and the journal.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, lastToast, requests, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, type Sent } from './server';

afterEach(stopAll);

const routes = (detail: Route[2] = invoice.invoice_draft): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, invoice.schema],
  ['GET', /\/api\/invoice\/7$/, detail],
  ['GET', /\/api\/invoice\/7\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/7\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/7\/related\/invoice\.revises/, invoice.invoice_related_revisions],
  ['GET', /\/api\/invoice\/7\/journal/, invoice.invoice_journal],
  ['GET', /\/api\/invoice\/7\/attachments$/, invoice.invoice_attachments],
  ['GET', /\/api\/customer\/1$/, { status: 200, etag: '"c1"', body: { id: 1, name: 'Al Sahra Trading', credit_days: 30 } }],
  ['GET', /\/api\/payment\/3$/, { status: 200, etag: '"p3"', body: { id: 3, invoice: 7, amount: '500.00', paid_on: '2026-11-10' } }],
];
const tabs = (host: Element) => [...host.querySelectorAll('.doc-tabs [role="tab"]')].map(text);
const banner = (host: Element) => text(host.querySelector('.inv-banner > div'));

describe('transaction document (provisional fixtures)', () => {
  it('shows a draft: number to come, status flow, the move, key fields, measures, journal and related records', async () => {
    const { host } = start(routes(), '/ui/m/invoice/7');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Draft invoice'));
    expect(host.querySelector('.doc-head h1')!.classList.contains('mono')).toBe(false);
    expect(text(host.querySelector('.rec-meta'))).toContain('Number assigned on issued');
    expect(banner(host)).toBe('Draft. Everything can change until it is issued.');
    expect([...host.querySelectorAll('.stepper .step')].map(text)).toEqual(['Draft', 'Issued', 'Paid', 'Void']);
    expect(host.querySelector('.stepper .step.cur')!.textContent).toBe('Draft');
    const issue = byText(host, '.doc-head button', 'Issue')!;
    expect(issue.classList.contains('primary')).toBe(true);
    expect(issue.hasAttribute('data-blocked')).toBe(false);
    // Record payment is listed for this role but blocked by its condition.
    expect(byText(host, '.doc-head button', 'Record payment')!.getAttribute('aria-disabled')).toBe('true');
    expect(tabs(host)).toEqual(['Activity', 'Details', 'Lines2', 'Related', 'Postings']);
    // Summary: key fields then per-record measures.
    await until(() => expect([...host.querySelectorAll('.sum-t')].map(item => `${text(item.querySelector('span'))}=${text(item.querySelector('b'))}`))
      .toEqual(['Customer=Al Sahra Trading', 'Total=AED 2,680.00', 'Due on=01-12-2026', 'Payments=1']));
    // Activity: the journal grouped by day, with field changes old and new.
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    expect(text(host.querySelector('.tl-item'))).toContain('sara moved it from Draft to Issued');
    expect(text(host.querySelectorAll('.tl-item')[1])).toContain('Remarks empty Net 30');
    expect(text(host.querySelector('.eff-note'))).toContain('Append-only history');
    // Side panel: key fields, related counts, files and record info.
    await until(() => expect(text(byText(host, '.side-sec', /^Related/))).toContain('Payments1 record'));
    expect(text(byText(host, '.side-sec', /^Files/))).toContain('signed-copy.pdf');
    click(byText(host, '.doc-tabs [role="tab"]', 'Related'));
    await until(() => expect(text(host.querySelector('.ev-related .ev-row-link'))).toBe('10-11-2026'));
    expect(location.search).toBe('?tab=related');
  });

  it('explains a blocked move instead of running it', async () => {
    const { host, sent } = start(routes(invoice.invoice_draft_blocked), '/ui/m/invoice/7');
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    const issue = byText(host, '.doc-head button', 'Issue')!;
    expect(issue.getAttribute('aria-disabled')).toBe('true');
    click(issue);
    expect(lastToast()).toBe('The condition for this move is not met');
    click(byText(host, '.stepper .step', 'Issued'));
    expect(lastToast()).toBe('The condition for this move is not met');
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
  });

  it('issues through the move route, then shows the number, the lock and the postings', async () => {
    const { host, sent } = start([...routes(), ['POST', /\/api\/invoice\/7\/moves\/issued$/, invoice.invoice_issued]], '/ui/m/invoice/7?tab=details');
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Issue'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('INV-2026-00042'));
    const move = requests(sent, 'POST', /moves\/issued$/)[0];
    expect(move.headers['if-match']).toBe('"inv7-1"');
    expect(move.body).toEqual({});
    expect(host.querySelector('.doc-head h1')!.classList.contains('mono')).toBe(true);
    expect(lastToast()).toBe('Issue done. Number INV-2026-00042.');
    expect(banner(host)).toBe('Issued and locked. Only remarks can change. To correct it, void or revise.');
    // Locked: only the field declared editable_when_locked stays editable.
    const editable = [...host.querySelectorAll('.fs .ff')].filter(item => item.querySelector('button.fval')).map(item => item.getAttribute('data-field'));
    expect(editable).toEqual(['remarks']);
    expect(text(host.querySelector('.ff[data-field="customer"] label'))).toContain('locked');
    expect(tabs(host)).toEqual(['Activity', 'Details', 'Lines2', 'Related', 'Postings3']);
    click(byText(host, '.doc-tabs [role="tab"]', /^Postings/));
    expect([...host.querySelectorAll('.doc-main .dt tbody td')].map(text)).toEqual(['Ledger entries', '3', '0', 'Balanced']);
    click(byText(host, '.doc-tabs [role="tab"]', /^Lines/));
    expect(text(host.querySelector('.g-lock'))).toContain('Locked.');
  });

  it('records a payment through the action dialog, checking the inputs first', async () => {
    let sentBody: Sent | null = null;
    const { host } = start([...routes(invoice.invoice_issued),
      ['POST', /\/api\/invoice\/7\/actions\/record_payment$/, sent => { sentBody = sent; return copy(invoice.invoice_issued); }]], '/ui/m/invoice/7');
    await until(() => expect(byText(host, '.doc-head button', 'Record payment')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Record payment'));
    const dialog = document.querySelector('.modal')!;
    expect([...dialog.querySelectorAll('.fld label')].map(item => text(item).replace('*', '').trim())).toEqual(['Amount', 'Paid on']);
    click(byText(dialog, '.modal-f button', 'Record payment'));
    expect([...dialog.querySelectorAll('.fld .err')].map(text)).toEqual(['Amount is required', 'Paid on is required']);
    expect(sentBody).toBeNull();
    const [amount, paidOn] = [...dialog.querySelectorAll<HTMLInputElement>('.fld input')];
    typeInto(amount, '500');
    typeInto(paidOn, '10-11-2026');
    click(byText(dialog, '.modal-f button', 'Record payment'));
    await until(() => expect(sentBody).not.toBeNull());
    expect(sentBody!.body).toEqual({ amount: '500.00', paid_on: '2026-11-10' });
    expect(sentBody!.headers['if-match']).toBe('"inv7-2"');
    await until(() => expect(document.querySelector('.modal')).toBeNull());
  });

  it('voids with a required reason and links to the replacement', async () => {
    const { host, sent } = start([...routes(invoice.invoice_issued), ['POST', /\/api\/invoice\/7\/void$/, invoice.invoice_void]], '/ui/m/invoice/7');
    await until(() => expect(byText(host, '.inv-banner button', 'Void')).not.toBeNull());
    click(byText(host, '.inv-banner button', 'Void'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe('Void INV-2026-00042');
    click(byText(dialog, '.modal-f button', 'Void invoice'));
    expect(text(dialog.querySelector('.err'))).toBe('Give a reason');
    expect(requests(sent, 'POST', /void$/)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Customer cancelled the order');
    click(byText(dialog, '.modal-f button', 'Void invoice'));
    await until(() => expect(banner(host)).toContain('Void.'));
    const request = requests(sent, 'POST', /void$/)[0];
    expect(request.body).toEqual({ reason: 'Customer cancelled the order' });
    expect(request.headers['if-match']).toBe('"inv7-2"');
    expect(host.querySelector<HTMLAnchorElement>('.inv-banner a')!.getAttribute('href')).toBe('/ui/m/invoice/8');
    expect(host.querySelector('.stepper .step.cur')!.classList.contains('lost')).toBe(true);
    expect(lastToast()).toBe('Voided. The number stays used.');
  });

  it('revises: voids, then opens the editable copy on its lines with a link back', async () => {
    const { host, sent } = start([...routes(invoice.invoice_issued),
      ['POST', /\/api\/invoice\/7\/revise$/, invoice.invoice_revision],
      ['GET', /\/api\/invoice\/8$/, { ...invoice.invoice_revision, status: 200 }],
      ['GET', /\/api\/invoice\/8\//, { status: 200, etag: null, body: { items: [], total: 0, limit: 10, offset: 0, measures: {} } }],
    ], '/ui/m/invoice/7');
    await until(() => expect(byText(host, '.inv-banner button', 'Revise')).not.toBeNull());
    click(byText(host, '.inv-banner button', 'Revise'));
    const dialog = document.querySelector('.modal')!;
    typeInto(dialog.querySelector('textarea')!, 'Wrong quantity on line 2');
    click(byText(dialog, '.modal-f button', 'Revise invoice'));
    await until(() => expect(location.pathname).toBe('/ui/m/invoice/8'));
    expect(location.search).toBe('?tab=t-lines');
    expect(requests(sent, 'POST', /revise$/)[0].body).toEqual({ reason: 'Wrong quantity on line 2' });
    await until(() => expect(banner(host)).toBe('Draft. Everything can change until it is issued. Revision of Invoice 7.'));
    expect(host.querySelector<HTMLAnchorElement>('.inv-banner a')!.getAttribute('href')).toBe('/ui/m/invoice/7');
    expect(host.querySelector('.doc-tabs [aria-selected="true"]')!.textContent).toContain('Lines');
  });
});
