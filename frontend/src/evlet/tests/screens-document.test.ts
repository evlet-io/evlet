// The record view of a transaction document over responses recorded from the engine as an editor:
// State steps and rules (void and reopen take a remark), Sequence, measures, meaning metadata, files and the
// activity.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, lastToast, requests, start, stopAll, headText, text, typeInto, typeRich, until, type Route } from './screens-helpers';
import { copy, type Recorded, type Sent } from './server';
import { openForm } from '../app/prompts';
import { SchemaIndex } from '../model/schema';
import type { AppContext } from '../app/context';

afterEach(stopAll);

// The draft that is issued and paid in part, and the invoice that is voided.
const draft = invoice.invoice_draft.body.id, voided = invoice.invoice_void.body.id;
const path = (id: number, rest = '$') => new RegExp(`/api/invoice/${id}${rest}`);
// The draft's activity as it was when the draft was read; every other detail replays the issued invoice's.
const routes = (detail: Recorded = invoice.invoice_draft, activity = detail === invoice.invoice_draft ? invoice.invoice_draft_activity : invoice.invoice_activity): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, invoice.schema],
  ['GET', path(detail.body.id), detail],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/activity/, activity],
  ['GET', /\/api\/invoice\/[0-9a-z]{13}\/attachments$/, invoice.invoice_attachments],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ['GET', new RegExp(`/api/payment/${invoice.invoice_payment.body.id}$`), invoice.invoice_payment],
];
/** The record's "more information" rows, by title. */
const rows = (host: Element) => [...host.querySelectorAll('.ev-more-row')].map(row => text(row.querySelector('.ev-more-toggle')));
/** Open a "more information" row by its title. */
const openRow = (host: Element, title: string) => click(host.querySelector(`.ev-more-row[data-section="${title}"] .ev-more-toggle`));
const banner = (host: Element) => text(host.querySelector('.inv-banner > div'));

describe('transaction document (recorded fixtures)', () => {
  it('displays server-owned action inputs without validating or submitting them', async () => {
    const submissions: Record<string, unknown>[] = [];
    // A form of a few fields (a move's remark and asked fields, an action's arguments) in the prompt modal.
    const ctx = { api: {}, schema: new SchemaIndex({ version: 1, ui_version: 1, models: [] }), user: null, links: null } as unknown as AppContext;
    void openForm(ctx, { title: 'Approve', fields: [
      { name: 'reason', label: 'Reason', type: 'text', required: true, read_only: false },
      { name: 'issuer', label: 'Issuer', type: 'text', required: true, read_only: true, default: 'Server' },
      { name: 'pending', label: 'Pending value', type: 'text', required: true, read_only: true },
      { name: 'flag', label: 'Flag', type: 'boolean', required: false, read_only: true, default: false },
      { name: 'number', label: 'Number', type: 'integer', required: false, read_only: true, default: 0 },
    ], run: async values => { submissions.push(values); return null; } });
    await until(() => expect(document.querySelector('.modal.prompt')).not.toBeNull());
    const dialog = document.querySelector('.modal')!;
    const issuer = dialog.querySelector<HTMLInputElement>('[data-field="issuer"] input')!;
    expect(issuer.disabled).toBe(true);
    expect(issuer.value).toBe('Server');
    expect(dialog.querySelector<HTMLInputElement>('[data-field="pending"] input')!.disabled).toBe(true);
    expect(dialog.querySelector<HTMLButtonElement>('[data-field="flag"] button')!.disabled).toBe(true);
    expect(dialog.querySelector<HTMLInputElement>('[data-field="number"] input')!.value).toBe('0');
    click(byText(dialog, '.modal-f button', 'Approve'));
    expect(submissions).toHaveLength(0);
    expect(text(dialog.querySelector('[data-field="reason"] .err'))).toBe('Reason is required');
    expect(text(dialog.querySelector('[data-field="pending"] .err'))).toBe('');
    typeInto(dialog.querySelector<HTMLInputElement>('[data-field="reason"] input')!, 'Reviewed');
    issuer.disabled = false;
    typeInto(issuer, 'Forged');
    click(byText(dialog, '.modal-f button', 'Approve'));
    await until(() => expect(submissions).toEqual([{ reason: 'Reviewed' }]));
    expect(document.querySelector('.modal')).toBeNull();
  });

  it('shows a draft: number to come, status flow, the move, key fields, measures, activity and related records', async () => {
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
    // No tabs: the Lines grid is in the primary area, the rest is more information, Activity is in the side column.
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(text(host.querySelector('.ev-cmp-main .ev-cmp-th'))).toMatch(/^Lines2/);
    expect(rows(host)).toEqual(['Amounts', 'Related']);
    // E3: the header's stage bar shows and moves the State, so the read form leaves it out.
    await until(() => expect(host.querySelector('.ev-cmp-card .ff')).not.toBeNull());
    expect(host.querySelector('.ev-cmp-card .ff[data-field="status"]')).toBeNull();
    // Key fields make the subtitle; the summary keeps what the header doesn't: per-record measures.
    // The customer is in the primary area, so the subtitle leaves it out: a value shows once per view.
    await until(() => expect(text(host.querySelector('.ev-cmp-main [data-field="customer"]'))).toContain('Al Sahra Trading'));
    expect(text(host.querySelector('.doc-head .doc-sub'))).not.toContain('Al Sahra Trading');
    await until(() => expect([...host.querySelectorAll('.sum-t')].map(item => `${text(item.querySelector('span'))}=${text(item.querySelector('b'))}`))
      .toEqual(['Payments=1', 'Paid=AED 500.00']));
    // Activity: the activity grouped by day, newest first, actors named from the page's users, field
    // changes old and new and Table rows counted.
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => headText(item));
    expect(items).toEqual(['sara attached signed-copy.pdf', 'sara changed it', 'sara created this record']);
    const changed = host.querySelectorAll('.tl-item')[1];
    expect([...changed.querySelectorAll('.ev-diff')].map(text)).toEqual(['Total AED 0.00 AED 2,680.00', 'Lines 2 added']);
    expect(host.querySelector('.ev-partial')).toBeNull();
    expect(text(host.querySelector('.eff-note'))).toContain('Append-only history');
    expect(host.querySelector('.composer .rt-surface')).not.toBeNull();
    // The side column: the record's files and its activity. The Related row counts related records while closed.
    await until(() => expect(text(byText(host, '.side-sec', /^Attachments/))).toContain('signed-copy.pdf'));
    await until(() => expect(text(host.querySelector('.ev-more-row[data-section="Related"] .ev-more-sum'))).toContain('Payments: 1'));
    openRow(host, 'Related');
    // A payment has no text field, so its title is its id.
    await until(() => expect(text(host.querySelector('.ev-related .ev-row-link'))).toBe(String(invoice.invoice_payment.body.id)));
  });

  it('explains a blocked move instead of running it', async () => {
    const { host, sent } = start(routes(invoice.invoice_draft_blocked), `/app/invoice/${draft}`);
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    const issue = byText(host, '.doc-head button', 'Issue')!;
    expect(issue.getAttribute('aria-disabled')).toBe('true');
    click(issue);
    expect(lastToast()).toBe('The condition for this move is not met');
    // The bar offers only moves this person can take now: with the guard not met, Issued is a plain step.
    expect(byText(host, '.stepper .step', 'Issued')!.querySelector('button')).toBeNull();
    click(byText(host, '.stepper .step', 'Issued'));
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
  });

  it('issues through the move route, then shows the number and the lock', async () => {
    const { host, sent } = start([...routes(), ['POST', path(draft, '/moves/issue$'), invoice.invoice_issued]], `/app/invoice/${draft}`);
    await until(() => expect(byText(host, '.doc-head button', 'Issue')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Issue'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe(invoice.invoice_issued.body.number));
    const move = requests(sent, 'POST', /moves\/issue$/)[0];
    expect(move.headers['if-match']).toBe(invoice.invoice_draft.etag);
    expect(move.body).toEqual({});
    expect(host.querySelector('.doc-head h1')!.classList.contains('mono')).toBe(true);
    expect(lastToast()).toBe(`Issue done. Number ${invoice.invoice_issued.body.number}.`);
    expect(banner(host)).toBe('Issued and locked. Only remarks can change.');
    // Locked: only the field declared versioned=False stays editable.
    const editable = [...host.querySelectorAll('.ev-cmp-card .ff')].filter(item => item.querySelector('button.fval')).map(item => item.getAttribute('data-field'));
    expect(editable).toEqual(['remarks']);
    // The number shows in the header (here the title); the form leaves it out, so no stray row repeats it.
    expect(host.querySelector('.ev-cmp-card [data-field="number"]')).toBeNull();
    // The banner says it is locked once; the fields carry no lock of their own.
    expect(host.querySelector('.ff[data-field="customer"] .ff-hint')).toBeNull();
    expect(host.querySelectorAll('.ev-cmp-card .ff-hint:not(.calc):not(.init)')).toHaveLength(0);
    expect(host.querySelector('.ev-more-row[data-section="Postings"]')).toBeNull();
    expect(text(host.querySelector('.ev-cmp-main .g-lock'))).toContain('Locked.');
  });

  it('records a payment through the prompt its inputs open, checking the inputs first', async () => {
    let submitted: Sent | null = null;
    const { host, sent } = start([...routes(invoice.invoice_issued_detail),
      ['POST', path(draft, '/actions/record_payment$'), copy(invoice.invoice_action_prompt)],
      ['POST', /\/api\/_prompts\/fixture-payment-ticket\/submit$/, item => { submitted = item; return copy(invoice.invoice_action_submit); }]], `/app/invoice/${draft}`);
    await until(() => expect(byText(host, '.doc-head button', 'Record payment')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Record payment'));
    await until(() => expect(document.querySelector('.modal.prompt')).not.toBeNull());
    // The action is sent without inputs (with the record's revision) and answers its prompt.
    const opened = requests(sent, 'POST', /actions\/record_payment$/)[0];
    expect(opened.body).toEqual({});
    expect(opened.headers['if-match']).toBe(invoice.invoice_issued_detail.etag);
    const dialog = document.querySelector('.modal')!;
    expect([...dialog.querySelectorAll('.fld label')].map(item => text(item).replace('*', '').trim())).toEqual(['Amount', 'Paid on']);
    (document.activeElement as HTMLElement | null)?.blur();
    const [amount, paidOn] = [...dialog.querySelectorAll<HTMLInputElement>('.fld input')];
    typeInto(amount, '500');
    typeInto(paidOn, '10-11-2026');
    click(byText(dialog, '.modal-f button', 'Record payment'));
    await until(() => expect(submitted).not.toBeNull());
    expect(submitted!.body.values).toEqual({ amount: '500.00', paid_on: '2026-11-10' });
    await until(() => expect(document.querySelector('.modal')).toBeNull());
  });

  it('voids through the void move, whose dialog asks for the required remark under its label', async () => {
    const before = invoice.invoice_void_before;
    const { host, sent } = start([...routes(before), ['POST', path(voided, '/moves/void$'), invoice.invoice_void]], `/app/invoice/${voided}`);
    await until(() => expect(byText(host, '.stepper .step', 'Void')!.querySelector('button')).not.toBeNull());
    click(byText(host, '.stepper .step', 'Void')!.querySelector('button'));
    await until(() => expect(document.querySelector('.modal.prompt')).not.toBeNull());
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe('Void');
    expect(text(dialog.querySelector('.fld label')).replace('*', '').trim()).toBe('Reason');
    click(byText(dialog, '.modal-f button', 'Void'));
    expect(text(dialog.querySelector('.err'))).toBe('Reason is required');
    expect(invoice.invoice_void_reason_missing.body.fields).toHaveProperty('remark');
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Customer cancelled the order');
    click(byText(dialog, '.modal-f button', 'Void'));
    await until(() => expect(banner(host)).toMatch(/^Void( on [^.]+)?\./));
    const request = requests(sent, 'POST', /moves\/void$/)[0];
    expect(request.body).toEqual({ remark: 'Customer cancelled the order' });
    expect(request.headers['if-match']).toBe(before.etag);
    expect(host.querySelector('.stepper .step.cur')!.classList.contains('lost')).toBe(true);
    expect(lastToast()).toBe('Moved to Void');
    // The reversal rows show beside the posted ones.
  });

  it('reopens an issued invoice as admin with a required remark', async () => {
    const before = invoice.invoice_reopen_before, reopened = invoice.invoice_reopen, id = before.body.id;
    const admin: Route[] = routes(before).map(route => route[1].source.includes('session') ? ['GET', route[1], invoice.invoice_admin_session] : route);
    const { host, sent } = start([...admin, ['POST', path(id, '/moves/reopen$'), reopened]], `/app/invoice/${id}`);
    await until(() => expect(byText(host, '.stepper .step', 'Draft')!.querySelector('button')).not.toBeNull());
    click(byText(host, '.stepper .step', 'Draft')!.querySelector('button'));
    await until(() => expect(document.querySelector('.modal.prompt')).not.toBeNull());
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe('Reopen');
    click(byText(dialog, '.modal-f button', 'Reopen'));
    expect(text(dialog.querySelector('.err'))).toBe('Remark is required');
    expect(invoice.invoice_reopen_remark_missing.status).toBe(422);
    expect(requests(sent, 'POST', /moves/)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Wrong customer');
    click(byText(dialog, '.modal-f button', 'Reopen'));
    await until(() => expect(banner(host)).toBe('Draft. Everything can change until it is issued.'));
    const request = requests(sent, 'POST', /moves\/reopen$/)[0];
    expect(request.body).toEqual({ remark: 'Wrong customer' });
    expect(request.headers['if-match']).toBe(before.etag);
    // It posted before, so it is kept: the More menu offers no Delete.
    expect(reopened.body._deletable).toBe(false);
    click(host.querySelector('.doc-head button[aria-label="More actions"]'));
    expect([...document.querySelectorAll('[role="menuitem"]')].map(text)).not.toContain('Delete');
    (document.activeElement as HTMLElement | null)?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    // The number stays; the reversal rows show beside the posted ones.
    expect(reopened.body.number).toBe(before.body.number);
  });

  it('offers no reopen to a role the rule does not name', async () => {
    const { host } = start(routes(invoice.invoice_reopen_editor), `/app/invoice/${invoice.invoice_reopen_editor.body.id}`);
    await until(() => expect(byText(host, '.stepper .step', 'Void')!.querySelector('button')).not.toBeNull());
    expect(byText(host, '.stepper .step', 'Draft')!.querySelector('button')).toBeNull();
    expect(banner(host)).toBe('Issued and locked. Only remarks can change.');
  });

  it('offers no upload and no remove on a locked record, whose files the server refuses to change', async () => {
    const { host } = start(routes(invoice.invoice_issued_detail), `/app/invoice/${draft}`);
    await until(() => expect(text(byText(host, '.side-sec', /^Attachments/))).toContain('signed-copy.pdf'));
    expect(host.querySelector('.ev-upload')).toBeNull();
    expect(host.querySelector('.ev-file-remove')).toBeNull();
    expect(invoice.invoice_attachment_locked.status).toBe(409);
  });

  it('shows a changed Link in the history as the linked record title, never as an amount', async () => {
    const activity = copy(invoice.invoice_draft_activity);
    const customer = invoice.invoice_customer.body.id;
    activity.body.items[1].data.fields = { customer: { old: null, new: customer }, ...activity.body.items[1].data.fields };
    const { host } = start(routes(invoice.invoice_draft, activity), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    const changed = host.querySelectorAll('.tl-item')[1];
    await until(() => expect([...changed.querySelectorAll('.ev-diff')].map(text)[0]).toBe('Customer empty Al Sahra Trading'));
    expect(text(changed)).not.toContain('Invalid amount');
  });

  it('shows the issued invoice activity as the engine records it: moves, numbers, actions and people', async () => {
    const { host } = start(routes(invoice.invoice_issued_detail), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(invoice.invoice_activity.body.items.length));
    const heads = [...host.querySelectorAll('.tl-item .hd')].map(item => headText(item));
    expect(heads).toEqual([
      'The system logged a message to the customer',
      'sara planned a follow-up',
      'sara completed a follow-up',
      'sara planned a follow-up',
      'sara added a note',
      'sara ran Record payment',
      `sara assigned the number ${invoice.invoice_issued.body.number}`,
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
    // The move lists what else it changed.
    expect(text(items[7].querySelector('.ev-diff'))).toBe('Outstanding AED 0.00 AED 2,680.00');
    expect(host.querySelector('.ev-partial')).toBeNull();
  });

  it('adds a note through the activity route and reloads the history', async () => {
    let reloaded = false;
    const { host, sent } = start([...routes(invoice.invoice_issued_detail),
      ['POST', path(draft, '/activity$'), () => { reloaded = true; return copy(invoice.invoice_activity_note); }]], `/app/invoice/${draft}`);
    await until(() => expect(host.querySelector('.composer .rt-surface')).not.toBeNull());
    typeRich(host.querySelector<HTMLElement>('.composer .rt-surface')!, 'Customer asked for a copy by email');
    click(byText(host, '.composer button', 'Save note'));
    await until(() => expect(lastToast()).toBe('Note added'));
    expect(reloaded).toBe(true);
    expect(requests(sent, 'POST', /\/activity$/)[0].body).toEqual({ kind: 'note', html: '<p>Customer asked for a copy by email</p>' });
    await until(() => expect(requests(sent, 'GET', /\/activity/)).toHaveLength(2));
    expect(host.querySelector('.composer .rt-surface')!.textContent).toBe('');
  });

  it('shows a viewer the activity, with no note box', async () => {
    const viewerRoutes: Route[] = [
      ['GET', /\/api\/auth\/session$/, invoice.invoice_viewer_session],
      ['GET', /\/api\/schema$/, invoice.invoice_viewer_schema],
      ...routes(invoice.invoice_viewer_detail, invoice.invoice_viewer_activity).slice(2),
    ];
    const { host, sent } = start(viewerRoutes, `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(invoice.invoice_viewer_activity.body.items.length));
    expect(host.querySelector('.composer')).toBeNull();
    // The viewer reads every field these entries name, so none is marked partial.
    expect([...host.querySelectorAll('.tl-item')].filter(item => item.querySelector('.ev-partial'))).toHaveLength(0);
    expect(requests(sent, 'POST', /\/activity$/)).toHaveLength(0);
    expect(invoice.invoice_viewer_note_refused.status).toBe(403);
  });

  it('names the AI assistant a change came through, after the person', async () => {
    const activity = copy(invoice.invoice_draft_activity);
    activity.body.items[1].via = 'Claude';
    const { host } = start(routes(invoice.invoice_draft, activity), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(3));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => headText(item));
    expect(items).toEqual(['sara attached signed-copy.pdf', 'sara changed it via Claude', 'sara created this record']);
  });

  it('shows emails from a personal mailbox, without an address the reader may not see', async () => {
    const activity = copy(invoice.invoice_draft_activity);
    const first = activity.body.items[0];
    activity.body.items = [{ ...first, id: 900, kind: 'message', data: { text: 'Can you ship Monday?', subject: 'Order', party: 'buyer@acme.test', direction: 'in', source: 'mailbox' } },
      { ...first, id: 901, kind: 'message', partial: true, data: { text: 'Thanks', subject: 'Re: Order', party: null, direction: 'out', source: 'mailbox' } }];
    activity.body.total = 2;
    const { host } = start(routes(invoice.invoice_draft, activity), `/app/invoice/${draft}`);
    await until(() => expect(host.querySelectorAll('.tl-item')).toHaveLength(2));
    const items = [...host.querySelectorAll('.tl-item .hd')].map(item => headText(item));
    expect(items).toEqual(['sara received an email from buyer@acme.test', 'sara sent an email to a contact']);
    expect(text(host.querySelector('.tl-item .note .subj'))).toBe('Order');
  });
});
