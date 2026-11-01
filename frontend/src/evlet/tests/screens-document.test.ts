// The record view of a transaction document over responses recorded from the engine as an editor:
// State and moves, Sequence, void and revise, effects, measures, meaning metadata and files. Only
// the journal (Phase 8) is provisional (see provisional/README.md).
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, lastToast, requests, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, type Recorded, type Sent } from './server';

afterEach(stopAll);

// The draft that is issued and paid in part, the invoice that is voided and the one that is revised.
const draft = invoice.invoice_draft.body.id, voided = invoice.invoice_void.body.id, revised = invoice.invoice_revise_before.body.id;
const copyId = invoice.invoice_revision.body.id;
const path = (id: number, rest = '$') => new RegExp(`/api/invoice/${id}${rest}`);
const routes = (detail: Recorded = invoice.invoice_draft): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, invoice.schema],
  ['GET', path(detail.body.id), detail],
  ['GET', /\/api\/invoice\/\d+\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/\d+\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/\d+\/related\/invoice\.revises/, invoice.invoice_related_revisions],
  ['GET', /\/api\/invoice\/\d+\/journal/, invoice.invoice_journal],
  ['GET', /\/api\/invoice\/\d+\/attachments$/, invoice.invoice_attachments],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ['GET', new RegExp(`/api/payment/${invoice.invoice_payment.body.id}$`), invoice.invoice_payment],
];
const tabs = (host: Element) => [...host.querySelectorAll('.doc-tabs [role="tab"]')].map(text);
const banner = (host: Element) => text(host.querySelector('.inv-banner > div'));

describe('transaction document (recorded fixtures)', () => {
  it('shows a draft: number to come, status flow, the move, key fields, measures, journal and related records', async () => {
    const { host } = start(routes(), `/app/invoice/${draft}`);
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
    // A payment has no text field, so its title is its id.
    await until(() => expect(text(host.querySelector('.ev-related .ev-row-link'))).toBe(String(invoice.invoice_payment.body.id)));
    expect(location.search).toBe('?tab=related');
  });

  it('explains a blocked move instead of running it', async () => {
    const { host, sent } = start(routes(invoice.invoice_draft_blocked), `/app/invoice/${draft}`);
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
    const { host, sent } = start([...routes(), ['POST', path(draft, '/moves/issued$'), invoice.invoice_issued]], `/app/invoice/${draft}?tab=details`);
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Issue'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe(invoice.invoice_issued.body.number));
    const move = requests(sent, 'POST', /moves\/issued$/)[0];
    expect(move.headers['if-match']).toBe(invoice.invoice_draft.etag);
    expect(move.body).toEqual({});
    expect(host.querySelector('.doc-head h1')!.classList.contains('mono')).toBe(true);
    expect(lastToast()).toBe(`Issue done. Number ${invoice.invoice_issued.body.number}.`);
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
    const { host } = start([...routes(invoice.invoice_issued_detail),
      ['POST', path(draft, '/actions/record_payment$'), sent => { sentBody = sent; return copy(invoice.invoice_action); }]], `/app/invoice/${draft}`);
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
    expect(sentBody!.headers['if-match']).toBe(invoice.invoice_issued_detail.etag);
    await until(() => expect(document.querySelector('.modal')).toBeNull());
  });

  it('voids with a required reason; a void has no replacement', async () => {
    const before = invoice.invoice_void_before;
    const { host, sent } = start([...routes(before), ['POST', path(voided, '/void$'), invoice.invoice_void]], `/app/invoice/${voided}`);
    await until(() => expect(byText(host, '.inv-banner button', 'Void')).not.toBeNull());
    click(byText(host, '.inv-banner button', 'Void'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe(`Void ${before.body.number}`);
    click(byText(dialog, '.modal-f button', 'Void invoice'));
    // The same words the server answers an empty reason with.
    expect(text(dialog.querySelector('.err'))).toBe(invoice.invoice_void_reason_missing.body.fields.reason);
    expect(requests(sent, 'POST', /void$/)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Customer cancelled the order');
    click(byText(dialog, '.modal-f button', 'Void invoice'));
    await until(() => expect(banner(host)).toContain('Void.'));
    const request = requests(sent, 'POST', /void$/)[0];
    expect(request.body).toEqual({ reason: 'Customer cancelled the order' });
    expect(request.headers['if-match']).toBe(before.etag);
    expect(invoice.invoice_void.body.revised_by).toBeNull();
    expect(host.querySelector('.inv-banner a')).toBeNull();
    expect(host.querySelector('.stepper .step.cur')!.classList.contains('lost')).toBe(true);
    expect(lastToast()).toBe('Voided. The number stays used.');
    // The reversal rows show beside the posted ones.
    click(byText(host, '.doc-tabs [role="tab"]', /^Postings/));
    expect([...host.querySelectorAll('.doc-main .dt tbody td')].map(text)).toEqual(['Ledger entries', '3', '3', 'Balanced']);
  });

  it('revises: voids, then opens the editable copy on its lines with a link back', async () => {
    const before = invoice.invoice_revise_before;
    const { host, sent } = start([...routes(before),
      ['POST', path(revised, '/revise$'), invoice.invoice_revision],
      ['GET', path(copyId), { ...invoice.invoice_revision, status: 200 }],
      ['GET', path(copyId, '/'), { status: 200, etag: null, body: { items: [], total: 0, limit: 10, offset: 0, measures: {} } }],
    ], `/app/invoice/${revised}`);
    await until(() => expect(byText(host, '.inv-banner button', 'Revise')).not.toBeNull());
    click(byText(host, '.inv-banner button', 'Revise'));
    const dialog = document.querySelector('.modal')!;
    typeInto(dialog.querySelector('textarea')!, 'Wrong quantity on line 2');
    click(byText(dialog, '.modal-f button', 'Revise invoice'));
    await until(() => expect(location.pathname).toBe(`/app/invoice/${copyId}`));
    expect(location.search).toBe('?tab=t-lines');
    const request = requests(sent, 'POST', /revise$/)[0];
    expect(request.body).toEqual({ reason: 'Wrong quantity on line 2' });
    expect(request.headers['if-match']).toBe(before.etag);
    await until(() => expect(banner(host)).toBe(`Draft. Everything can change until it is issued. Revision of Invoice ${revised}.`));
    expect(host.querySelector<HTMLAnchorElement>('.inv-banner a')!.getAttribute('href')).toBe(`/app/invoice/${revised}`);
    expect(host.querySelector('.doc-tabs [aria-selected="true"]')!.textContent).toContain('Lines');
  });

  it('links a revised invoice to the copy that replaced it', async () => {
    const { host } = start(routes(invoice.invoice_revised_original), `/app/invoice/${revised}`);
    await until(() => expect(banner(host)).toContain('Void.'));
    expect(host.querySelector<HTMLAnchorElement>('.inv-banner a')!.getAttribute('href')).toBe(`/app/invoice/${copyId}`);
    // Already revised: neither correction is offered again.
    expect(byText(host, '.inv-banner button', 'Revise')).toBeNull();
    expect(byText(host, '.inv-banner button', 'Void')).toBeNull();
  });

  it('offers no upload on a locked record, whose files the server refuses to change', async () => {
    const { host } = start(routes(invoice.invoice_issued_detail), `/app/invoice/${draft}`);
    await until(() => expect(text(byText(host, '.side-sec', /^Files/))).toContain('signed-copy.pdf'));
    expect(host.querySelector('.ev-upload')).toBeNull();
    expect(invoice.invoice_attachment_locked.status).toBe(409);
  });
});
