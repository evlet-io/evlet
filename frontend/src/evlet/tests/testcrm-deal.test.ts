// testCRM end to end in jsdom: a deal's record view over responses recorded from the engine
// (scripts/generate_testcrm_fixtures.py). Lines, totals, rules, moves, the win that creates a task,
// paging, conflicts and what each role may see.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, lastToast, press, stopAll, text, typeInto, until } from './screens-helpers';
import { idOf, ids, scenario } from './testcrm-helpers';

afterEach(stopAll);

const rows = (host: Element) => [...host.querySelectorAll<HTMLElement>('.doc-main tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => row.querySelector<HTMLElement>(`td[data-col="${col}"]`)!;
const sums = (host: Element) => text(host.querySelector('.g-sum'));
const button = (host: Element, label: string) => byText<HTMLButtonElement>(host, '.doc-head button', label)!;
const current = (host: Element) => text(host.querySelector('.stepper .step.cur'));
const field = (host: Element, name: string) => host.querySelector<HTMLElement>(`.ff[data-field="${name}"]`);
const tab = (host: Element, label: RegExp) => click(byText(host, '.doc-tabs [role="tab"]', label));

function edit(cell: HTMLElement, value: string) {
  click(cell);
  const input = cell.querySelector<HTMLInputElement>('input')!;
  typeInto(input, value);
  press(input, 'Enter');
}

describe('testCRM: a deal and its lines', () => {
  it('edits, adds, reorders and removes lines; totals follow at once and after the save', async () => {
    const run = scenario('sara_win');
    const id = idOf('sara_win', 'deal', 'Production planning module');
    const { host } = run.start(`/app/deal/${id}?tab=t-lines`);
    await until(() => expect(rows(host)).toHaveLength(2));
    expect(text(host.querySelector('.doc-head h1'))).toBe('Production planning module');
    expect(sums(host)).toBe('SubtotalAED 83,400.00Vat totalAED 4,170.00TotalAED 87,570.00');
    // The footer totals each column by its plain sum, not the recurring (filtered) one.
    expect([...host.querySelectorAll('.doc-main tfoot td')].map(text).slice(-2)).toEqual(['AED 83,400.00', 'AED 4,170.00']);

    edit(cellOf(rows(host)[1], 'qty'), '15');
    expect(cellOf(rows(host)[1], 'amount').textContent!.trim()).toBe('AED 48,000.00');
    expect(sums(host)).toBe('SubtotalAED 93,000.00Vat totalAED 4,650.00TotalAED 97,650.00');
    press(document.activeElement!, 'Escape');

    // A new line from a product: description, rate and taxable come from the product.
    click(byText(host, '.doc-main button', 'Add row'));
    await until(() => expect(rows(host)).toHaveLength(3));
    click(cellOf(rows(host)[2], 'product'));
    click(cellOf(rows(host)[2], 'product').querySelector('button'));
    await until(() => expect(document.querySelector('.pop input')).not.toBeNull());
    typeInto(document.querySelector<HTMLInputElement>('.pop input')!, 'Training');
    await until(() => expect(byText(document.body, '.pop [role="option"]', /Training day/)).not.toBeNull());
    click(byText(document.body, '.pop [role="option"]', /Training day/));
    await until(() => expect(cellOf(rows(host)[2], 'rate').textContent!.trim()).toBe('AED 2,400.00'));
    expect(cellOf(rows(host)[2], 'description').textContent!.trim()).toBe('Training day');
    expect(sums(host)).toBe('SubtotalAED 95,400.00Vat totalAED 4,770.00TotalAED 100,170.00');

    // Alt and an arrow moves a line; it goes to the top.
    press(cellOf(rows(host)[2], 'qty'), 'ArrowUp', { altKey: true });
    press(cellOf(rows(host)[1], 'qty'), 'ArrowUp', { altKey: true });
    expect(rows(host).map(row => text(cellOf(row, 'description')))).toEqual(['Training day', 'ERP core licence', 'Implementation day']);

    click(byText(host, 'button', 'Save'));
    await until(() => expect(run.phase()).toBe(1));
    await until(() => expect(lastToast()).toContain('Saved'));
    const saved = run.writes[0];
    expect(saved.method).toBe('PATCH');
    const lines = saved.body.children.lines;
    expect(lines.update).toEqual([expect.objectContaining({ qty: '15.00' })]);
    expect(lines.create).toEqual([expect.objectContaining({ product: expect.any(Number) })]);
    expect(lines.order).toHaveLength(3);
    expect(saved.body.subtotal).toBeUndefined();
    await until(() => expect(sums(host)).toBe('SubtotalAED 95,400.00Vat totalAED 4,770.00TotalAED 100,170.00'));
    expect(rows(host).map(row => text(cellOf(row, 'description')))).toEqual(['Training day', 'ERP core licence', 'Implementation day']);

    // Remove a line with its checkbox; Undo is offered.
    rows(host)[0].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    click(byText(host, '.doc-main button', 'Delete'));
    expect(rows(host)).toHaveLength(2);
    expect(lastToast()).toContain('Deleted 1 row');
    expect(sums(host)).toBe('SubtotalAED 93,000.00Vat totalAED 4,650.00TotalAED 97,650.00');
    expect(run.misses).toEqual([]);
  });

  it('wins through its moves: the lock, the posting and the kick-off task the win created', async () => {
    const run = scenario('sara_win');
    const id = idOf('sara_win', 'deal', 'Production planning module');
    // Start after the line save the first test makes.
    run.skip(1);
    const { host } = run.start(`/app/deal/${id}`);
    await until(() => expect(button(host, 'Negotiate')).not.toBeNull());
    click(button(host, 'Negotiate'));
    await until(() => expect(current(host)).toBe('Negotiation'));
    expect(lastToast()).toContain('Negotiation');
    click(button(host, 'Mark won'));
    await until(() => expect(current(host)).toBe('Won'));
    // A sales person cannot void or revise, so the banner does not offer it.
    expect(text(host.querySelector('.inv-banner > div'))).toBe('Won and locked. Only lost reason and next step can change.');
    expect(run.writes.map(write => `${write.method} ${new URL(write.url, 'http://x').pathname}`)).toEqual([`POST /api/deal/${id}/moves/negotiation`, `POST /api/deal/${id}/moves/won`]);
    expect(run.writes.every(write => write.headers['if-match'] && JSON.stringify(write.body) === '{}')).toBe(true);
    // The win created a kick-off task: the Related block counts it again after the move.
    await until(() => expect(text(byText(host, '.side-sec', /^Related/))).toContain('Tasks2 records'));
    tab(host, /^Lines/);
    expect(text(host.querySelector('.g-lock'))).toBe('Locked. Lines can’t change while it is won.');
    expect(host.querySelector('.doc-main [aria-label="Add row"], .doc-main .g-add')).toBeNull();
    tab(host, /^Postings/);
    expect(text(host.querySelector('.doc-main'))).toContain('Bookings');
    tab(host, /^Related/);
    await until(() => expect(text(host.querySelector('.ev-related'))).toContain('Kick-off: Production planning module'));
    // Schedule follow up is listed but not available on a won deal.
    expect(button(host, 'Schedule follow up').getAttribute('aria-disabled')).toBe('true');
    expect(run.misses).toEqual([]);
  });

  it('explains a guarded move, then loses the deal and asks for the reason', async () => {
    const run = scenario('omar_lost');
    const id = idOf('omar_lost', 'deal', 'Shipment tracking integration');
    run.skip(1);
    const { host } = run.start(`/app/deal/${id}?tab=details`);
    await until(() => expect(current(host)).toBe('Qualified'));
    const proposal = button(host, 'Send proposal');
    expect(proposal.getAttribute('aria-disabled')).toBe('true');
    click(proposal);
    expect(lastToast()).toBe('The condition for this move is not met');
    expect(run.writes).toHaveLength(0);
    // The lost reason applies only once the deal is lost.
    expect(field(host, 'lost_reason')!.hidden).toBe(true);
    run.skip(1);
    // Mark lost asks for the reason in a small dialog and sends it with the move.
    click(button(host, 'Mark lost'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('h2'))).toBe('Mark lost');
    typeInto(dialog.querySelector('input')!, 'Budget moved');
    click(byText(dialog, '.modal-f button', 'Mark lost'));
    await until(() => expect(current(host)).toBe('Lost'));
    expect(run.writes.at(-1)!.body).toEqual({ lost_reason: 'Budget moved' });
    // Lost is not voidable, so the banner offers no correction.
    expect(text(host.querySelector('.inv-banner > div'))).toBe('Lost and locked. Only lost reason and next step can change.');
    expect(document.querySelector('.modal')).toBeNull();
    expect(field(host, 'lost_reason')!.hidden).toBe(false);
    // Locked, but the reason stays editable in place.
    expect([...host.querySelectorAll('.fs .ff')].filter(item => item.querySelector('button.fval, .fval:not(.ro)')).map(item => item.getAttribute('data-field')))
      .toEqual(expect.arrayContaining(['lost_reason', 'next_step']));
    const value = field(host, 'lost_reason')!.querySelector<HTMLElement>('.fval')!;
    click(value);
    const input = field(host, 'lost_reason')!.querySelector<HTMLInputElement>('input')!;
    typeInto(input, 'Budget moved to next year');
    press(input, 'Enter');
    await until(() => expect(run.writes.at(-1)?.body).toMatchObject({ lost_reason: 'Budget moved to next year' }));
    expect(Object.keys(run.writes.at(-1)!.body).filter(name => !name.startsWith('_'))).toEqual(['lost_reason']);
    await until(() => expect(text(field(host, 'lost_reason'))).toContain('Budget moved to next year'));
    // Reopen is for administrators: shown, blocked, with the reason.
    click(button(host, 'Reopen'));
    expect(lastToast()).toBe('Your role cannot use this move');
    expect(run.misses).toEqual([]);
  });

  it('asks for the lead reason before Mark lost and shows the server check in the dialog', async () => {
    const run = scenario('sara_lead_lost');
    const id = idOf('sara_lead_lost', 'lead', 'Majid Obaid');
    const { host } = run.start(`/app/lead/${id}?tab=details`);
    await until(() => expect(button(host, 'Mark lost')).not.toBeNull());
    click(button(host, 'Mark lost'));
    const dialog = document.querySelector('.modal')!;
    const submit = () => click(byText(dialog, '.modal-f button', 'Mark lost'));
    // Required in the target state: checked in the dialog before anything is sent.
    submit();
    expect(text(dialog)).toContain('Lost reason is required');
    expect(run.writes).toHaveLength(0);
    // The recording's first answer is the server's 422; its message lands on the field.
    typeInto(dialog.querySelector('input')!, 'No budget this year');
    submit();
    await until(() => expect(text(dialog)).toContain('This field is required'));
    expect(current(host)).toBe('New');
    submit();
    await until(() => expect(current(host)).toBe('Lost'));
    expect(run.writes.map(sent => sent.body)).toEqual([{ lost_reason: 'No budget this year' }, { lost_reason: 'No budget this year' }]);
    await until(() => expect(text(field(host, 'lost_reason'))).toContain('No budget this year'));
    expect(run.misses).toEqual([]);
  });
});

describe('testCRM: lead actions', () => {
  it('Convert to deal sends the typed date even when the field was not left before submitting', async () => {
    const run = scenario('sara_convert');
    const id = idOf('sara_convert', 'lead', 'Anjali Varma');
    const { host } = run.start(`/app/lead/${id}?tab=details`);
    await until(() => expect(button(host, 'Convert to deal')).not.toBeNull());
    click(button(host, 'Convert to deal'));
    const dialog = document.querySelector('.modal')!;
    const [title, close] = [...dialog.querySelectorAll<HTMLInputElement>('.fld input')];
    typeInto(title, 'Pharmacy chain ERP');
    // Typed, still in the field: no change event yet.
    close.focus();
    close.value = '15-12-2026';
    close.dispatchEvent(new Event('input', { bubbles: true }));
    click(byText(dialog, '.modal-f button', 'Convert to deal'));
    await until(() => expect(run.writes).toHaveLength(1));
    expect(run.writes[0].body).toEqual({ title: 'Pharmacy chain ERP', expected_close: '2026-12-15' });
    await until(() => expect(document.querySelector('.modal')).toBeNull());
    expect(run.misses).toEqual([]);
  });
});

describe('testCRM: 150 lines, conflicts and roles', () => {
  it('pages in 150 lines, edits the last one and saves only that change', async () => {
    const run = scenario('sara_big');
    const id = idOf('sara_big', 'deal', 'Group rollout, 150 sites');
    const { host } = run.start(`/app/deal/${id}?tab=t-lines`);
    await until(() => expect(text(host.querySelector('.doc-tabs'))).toContain('Lines150'));
    await until(() => expect(text(host.querySelector('.g-t .n'))).toBe('150'));
    // Only the rows in view are in the DOM.
    expect(rows(host).length).toBeLessThan(60);
    const before = sums(host);
    const scroller = host.querySelector<HTMLElement>('.doc-main .g-scroll') ?? host.querySelector<HTMLElement>('.doc-main table')!.parentElement!;
    await until(() => {
      scroller.scrollTop = 38 * 145;
      scroller.dispatchEvent(new Event('scroll'));
      expect(rows(host).some(row => text(cellOf(row, 'description')) === 'Site 0150')).toBe(true);
    });
    const last = rows(host).find(row => text(cellOf(row, 'description')) === 'Site 0150')!;
    edit(cellOf(last, 'qty'), '10');
    expect(sums(host)).not.toBe(before);
    const instant = sums(host);
    click(byText(host, 'button', 'Save'));
    await until(() => expect(run.writes).toHaveLength(1));
    expect(run.writes[0].body).toMatchObject({ children: { lines: { update: [{ id: expect.any(Number), qty: '10.00' }] } } });
    expect(Object.keys(run.writes[0].body.children.lines)).toEqual(['update']);
    await until(() => expect(host.querySelector('.save-bar, [aria-label="Unsaved changes"]')).toBeNull());
    expect(sums(host)).toBe(instant);
    expect(run.misses).toEqual([]);
  });

  it('two sessions: the later save is a conflict; Reload keeps the typed title to save again', async () => {
    const run = scenario('sara_conflict');
    const id = idOf('sara_conflict', 'deal', 'ERP rollout, 3 entities');
    const { host } = run.start(`/app/deal/${id}?tab=details`);
    await until(() => expect(field(host, 'title')).not.toBeNull());
    click(field(host, 'title')!.querySelector('.fval'));
    const input = field(host, 'title')!.querySelector<HTMLInputElement>('input')!;
    typeInto(input, 'ERP rollout, 3 entities and Oman');
    press(input, 'Enter');
    await until(() => expect(text(field(host, 'title'))).toContain('Someone else changed this record.'));
    click(byText(field(host, 'title')!, 'button', 'Reload'));
    await until(() => expect(text(field(host, 'next_step'))).toContain('Changed elsewhere'));
    click(byText(host, 'button', 'Save'));
    await until(() => expect(run.writes).toHaveLength(2));
    expect(run.writes[1].body).toMatchObject({ title: 'ERP rollout, 3 entities and Oman' });
    expect(Object.keys(run.writes[1].body).filter(name => !name.startsWith('_'))).toEqual(['title']);
    expect(run.writes[1].headers['if-match']).not.toBe(run.writes[0].headers['if-match']);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('ERP rollout, 3 entities and Oman'));
    expect(run.misses).toEqual([]);
  });

  it('a sales person cannot open another person\'s deal; viewers never see internal notes, admins do', async () => {
    const scoped = scenario('sara_scope');
    const theirs = scoped.start(`/app/deal/${ids.theirs}`);
    await until(() => expect(text(theirs.host.querySelector('main'))).toContain('Deal not found'));
    stopAll();
    const id = ids.deal;
    const viewer = scenario('vera_deal').start(`/app/deal/${id}?tab=details`);
    await until(() => expect(field(viewer.host, 'title')).not.toBeNull());
    expect(field(viewer.host, 'internal_notes')).toBeNull();
    expect(text(viewer.host.querySelector('.doc-main'))).not.toContain('Partner sign-off');
    // Nothing is editable for a viewer.
    expect(viewer.host.querySelector('.fs .fval:not(.ro)')).toBeNull();
    stopAll();
    const admin = scenario('admin_deal').start(`/app/deal/${id}?tab=details`);
    await until(() => expect(field(admin.host, 'internal_notes')).not.toBeNull());
    expect(text(field(admin.host, 'internal_notes'))).toContain('Partner sign-off needed');
  });
});

describe('testCRM: activity', () => {
  it('opens on the Activity tab with the moves, the win, its Booking posting and the follow up', async () => {
    const run = scenario('sara_win');
    const id = idOf('sara_win', 'deal', 'Production planning module');
    // After the line save, Negotiate and Mark won.
    run.skip(3);
    const { host } = run.start(`/app/deal/${id}`);
    await until(() => expect(text(host.querySelector('.doc-tabs [role="tab"]'))).toBe('Activity'));
    await until(() => expect(host.querySelectorAll('.tl-item').length).toBeGreaterThan(6));
    const heads = [...host.querySelectorAll('.tl-item .hd')].map(item => text(item).replace(/\d\d:\d\d.*$/, ''));
    expect(heads.slice(0, 5)).toEqual([
      'sara completed a follow-up',
      'sara planned a follow-up',
      'sara posted 1 row to Bookings',
      'sara moved it from Negotiation to Won',
      'sara moved it from Proposal to Negotiation',
    ]);
    expect(heads.slice(-2)).toEqual([expect.stringMatching(/^sara assigned the number DL-\d{4}-\d{4}$/), 'sara created this record']);
    expect(heads.filter(head => head.startsWith('sara moved it'))).toHaveLength(4);
    const items = [...host.querySelectorAll('.tl-item')];
    // The win set the kick-off as the next step and dated the deal.
    const won = [...items[3].querySelectorAll('.ev-diff')].map(text);
    expect(won).toContain('Next step empty Kick-off meeting');
    expect(won.some(line => line.startsWith('Won on empty '))).toBe(true);
    expect(text(items[1].querySelector('.ev-fu'))).toBe('Due 20-11-2026 for sara · Done');
    expect(text(items[0].querySelector('.note'))).toBe('Plan sent');
    // The line save is a change with its rows counted.
    expect([...host.querySelectorAll('.ev-diff')].map(text)).toEqual(expect.arrayContaining(['Lines 1 added, 1 updated', 'Subtotal AED 83,400.00 AED 95,400.00']));
    expect(host.querySelector('.ev-partial')).toBeNull();
    // A note goes to the journal and the history is read again.
    typeInto(host.querySelector<HTMLTextAreaElement>('.composer textarea')!, 'Kick-off agreed for next week');
    click(byText(host, '.composer button', 'Save note'));
    await until(() => expect(lastToast()).toBe('Note added'));
    expect(run.writes.at(-1)!.body).toEqual({ kind: 'note', text: 'Kick-off agreed for next week' });
    await until(() => expect(text(host.querySelector('.tl-item .hd')).replace(/\d\d:\d\d.*$/, '')).toBe('sara added a note'));
    expect(text(host.querySelector('.tl-item .note'))).toBe('Kick-off agreed for next week');
    expect(run.misses).toEqual([]);
  });
});
