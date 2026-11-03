// The record view of a transaction document over responses recorded from the engine as an editor:
// State and moves, Sequence, void and revise, effects, measures, meaning metadata, files and the
// journal.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, lastToast, requests, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, type Recorded, type Sent } from './server';

afterEach(stopAll);

// The draft that is issued and paid in part, the invoice that is voided and the one that is revised.
const draft = invoice.invoice_draft.body.id, voided = invoice.invoice_void.body.id, revised = invoice.invoice_revise_before.body.id;
const copyId = invoice.invoice_revision.body.id;
const path = (id: number, rest = '$') => new RegExp(`/api/invoice/${id}${rest}`);
// The draft's journal as it was when the draft was read; every other detail replays the issued invoice's.
const routes = (detail: Recorded = invoice.invoice_draft, journal = detail === invoice.invoice_draft ? invoice.invoice_draft_journal : invoice.invoice_journal): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, invoice.schema],
  ['GET', path(detail.body.id), detail],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/related\/invoice\.revises/, invoice.invoice_related_revisions],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/journal/, journal],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/attachments$/, invoice.invoice_attachments],
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
    // Activity: the journal grouped by day, newest first, actors named from the page's users, field
    // changes old and new and Table rows counted.
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => text(item).replace(/\d\d:\d\d.*$/, ''));
    expect(items).toEqual(['sara attached signed-copy.pdf', 'sara changed it', 'sara created this record']);
    const changed = host.querySelectorAll('.tl-item')[1];
    expect([...changed.querySelectorAll('.ev-diff')].map(text)).toEqual(['Total AED 0.00 AED 2,680.00', 'Lines 2 added']);
    expect(host.querySelector('.ev-partial')).toBeNull();
    expect(text(host.querySelector('.eff-note'))).toContain('Append-only history');
    expect(host.querySelector('.composer textarea')).not.toBeNull();
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

  it('unposts with a required reason as admin, then reposts from the banner', async () => {
    const before = invoice.invoice_unpost_before, unposted = invoice.invoice_unpost, id = before.body.id;
    const admin: Route[] = routes(before).map(route => route[1].source.includes('session') ? ['GET', route[1], invoice.invoice_admin_session] : route);
    const { host, sent } = start([...admin, ['POST', path(id, '/unpost$'), unposted], ['POST', path(id, '/repost$'), invoice.invoice_repost]],
      `/app/invoice/${id}`);
    await until(() => expect(byText(host, '.inv-banner button', 'Unpost')).not.toBeNull());
    click(byText(host, '.inv-banner button', 'Unpost'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe(`Unpost ${before.body.number}`);
    click(byText(dialog, '.modal-f button', 'Unpost invoice'));
    expect(text(dialog.querySelector('.err'))).toBe(invoice.invoice_unpost_reason_missing.body.fields.reason);
    expect(requests(sent, 'POST', /unpost$/)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Wrong customer');
    click(byText(dialog, '.modal-f button', 'Unpost invoice'));
    await until(() => expect(banner(host)).toContain('Draft, unposted.'));
    const request = requests(sent, 'POST', /unpost$/)[0];
    expect(request.body).toEqual({ reason: 'Wrong customer' });
    expect(request.headers['if-match']).toBe(before.etag);
    expect(lastToast()).toBe('Unposted. What it posted was reversed.');
    // It posted before, so it is kept: the More menu offers no Delete.
    expect(unposted.body._deletable).toBe(false);
    click(host.querySelector('.doc-head button[aria-label="More actions"]'));
    const menu = [...document.querySelectorAll('[role="menuitem"]')].map(text);
    expect(menu).toContain('Repost invoice');
    expect(menu).not.toContain('Delete');
    (document.activeElement as HTMLElement | null)?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    // The number stays; the reversal rows show beside the posted ones.
    expect(unposted.body.number).toBe(before.body.number);
    click(byText(host, '.doc-tabs [role="tab"]', /^Postings/));
    expect([...host.querySelectorAll('.doc-main .dt tbody td')].map(text)).toEqual(['Ledger entries', '3', '3', 'Balanced']);
    // Repost needs no reason.
    click(byText(host, '.inv-banner button', 'Repost'));
    const again = document.querySelector('.modal')!;
    expect(text(again.querySelector('h2'))).toBe(`Repost ${before.body.number}`);
    click(byText(again, '.modal-f button', 'Repost invoice'));
    await until(() => expect(banner(host)).toContain('Issued and locked.'));
    const repost = requests(sent, 'POST', /repost$/)[0];
    expect(repost.body).toEqual({});
    expect(repost.headers['if-match']).toBe(unposted.etag);
    expect(lastToast()).toBe('Reposted.');
  });

  it('offers no unpost to a role without it', async () => {
    const { host } = start(routes(invoice.invoice_unpost_editor), `/app/invoice/${invoice.invoice_unpost_editor.body.id}`);
    await until(() => expect(byText(host, '.inv-banner button', 'Void')).not.toBeNull());
    expect(byText(host, '.inv-banner button', 'Unpost')).toBeNull();
    expect(banner(host)).toContain('To correct it, void or revise.');
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

  it('shows the issued invoice journal as the engine records it: moves, postings, numbers, actions and people', async () => {
    const { host } = start(routes(invoice.invoice_issued_detail), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(invoice.invoice_journal.body.items.length));
    const heads = [...host.querySelectorAll('.tl-item .hd')].map(item => text(item).replace(/\d\d:\d\d.*$/, ''));
    expect(heads).toEqual([
      'The system logged a message to the customer',
      'sara planned a follow-up',
      'sara completed a follow-up',
      'sara planned a follow-up',
      'sara added a note',
      'sara ran Record payment',
      `sara assigned the number ${invoice.invoice_issued.body.number}`,
      'sara posted 3 rows to Ledger entries',
      'sara moved it from Draft to Issued',
      'sara attached signed-copy.pdf',
      'sara changed it',
      'sara created this record',
    ]);
    const items = [...host.querySelectorAll('.tl-item')];
    // A logged message keeps its subject; follow ups show the due date, the assignee and whether they are done.
    expect(text(items[0].querySelector('.note'))).toBe('Balance duePayment reminder logged');
    expect(text(items[1].querySelector('.ev-fu'))).toBe('Due 30-11-2026 · Open');
    expect(text(items[3].querySelector('.ev-fu'))).toBe('Due 15-11-2026 for sara · Done');
    expect(text(items[2].querySelector('.note'))).toBe('Balance due by the end of the month');
    expect(text(items[5].querySelector('.ev-diff'))).toBe('Remarks empty Paid 500.00 on 2026-11-10');
    // The posting and its balance check; the move lists what else it changed.
    expect(text(items[7].querySelector('.note'))).toBe('Ledger entries balanced');
    expect(text(items[8].querySelector('.ev-diff'))).toBe('Outstanding AED 0.00 AED 2,680.00');
    expect(host.querySelector('.ev-partial')).toBeNull();
  });

  it('adds a note through the journal route and reloads the history', async () => {
    let reloaded = false;
    const { host, sent } = start([...routes(invoice.invoice_issued_detail),
      ['POST', path(draft, '/journal$'), () => { reloaded = true; return copy(invoice.invoice_journal_note); }]], `/app/invoice/${draft}`);
    await until(() => expect(host.querySelector('.composer textarea')).not.toBeNull());
    typeInto(host.querySelector<HTMLTextAreaElement>('.composer textarea')!, 'Customer asked for a copy by email');
    click(byText(host, '.composer button', 'Save note'));
    await until(() => expect(lastToast()).toBe('Note added'));
    expect(reloaded).toBe(true);
    expect(requests(sent, 'POST', /\/journal$/)[0].body).toEqual({ kind: 'note', text: 'Customer asked for a copy by email' });
    await until(() => expect(requests(sent, 'GET', /\/journal/)).toHaveLength(2));
    expect((host.querySelector('.composer textarea') as HTMLTextAreaElement).value).toBe('');
  });

  it('shows a viewer the entries it may only partly see, with no note box', async () => {
    const viewerRoutes: Route[] = [
      ['GET', /\/api\/auth\/session$/, invoice.invoice_viewer_session],
      ['GET', /\/api\/schema$/, invoice.invoice_viewer_schema],
      ...routes(invoice.invoice_viewer_detail, invoice.invoice_viewer_journal).slice(2),
    ];
    const { host, sent } = start(viewerRoutes, `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(invoice.invoice_viewer_journal.body.items.length));
    expect(host.querySelector('.composer')).toBeNull();
    // The viewer cannot read ledger entries: the posting is a `changed` entry with nothing in it, marked partial.
    const partial = [...host.querySelectorAll('.tl-item')].filter(item => item.querySelector('.ev-partial'));
    expect(partial).toHaveLength(1);
    expect(text(partial[0].querySelector('.hd')).replace(/\d\d:\d\d.*$/, '')).toBe('sara changed it');
    expect(text(partial[0].querySelector('.ev-partial'))).toBe('Some details are hidden');
    expect(partial[0].querySelector('.note')).toBeNull();
    expect(requests(sent, 'POST', /\/journal$/)).toHaveLength(0);
    expect(invoice.invoice_viewer_note_refused.status).toBe(403);
  });

  it('names the AI assistant a change came through, after the person', async () => {
    const journal = copy(invoice.invoice_draft_journal);
    journal.body.items[1].via = 'Claude';
    const { host } = start(routes(invoice.invoice_draft, journal), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => text(item).replace(/\d\d:\d\d.*$/, ''));
    expect(items).toEqual(['sara attached signed-copy.pdf', 'sara changed it via Claude', 'sara created this record']);
  });

  it('shows emails from a personal mailbox, without an address the reader may not see', async () => {
    const journal = copy(invoice.invoice_draft_journal);
    const first = journal.body.items[0];
    journal.body.items = [{ ...first, id: 900, kind: 'message', data: { text: 'Can you ship Monday?', subject: 'Order', party: 'buyer@acme.test', direction: 'in', source: 'mailbox' } },
      { ...first, id: 901, kind: 'message', partial: true, data: { text: 'Thanks', subject: 'Re: Order', party: null, direction: 'out', source: 'mailbox' } }];
    journal.body.total = 2;
    const { host } = start(routes(invoice.invoice_draft, journal), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(2));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => text(item).replace(/\d\d:\d\d.*$/, ''));
    expect(items).toEqual(['sara received an email from buyer@acme.test', 'sara sent an email to a contact']);
    expect(text(host.querySelector('.tl-item .note .subj'))).toBe('Order');
  });
});
