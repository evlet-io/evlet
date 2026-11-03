import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, lastToast, press, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, id } from './server';
import { choose } from './ui-helpers';

afterEach(() => stopAll());

// Responses recorded from the real slip routes (record_slips in scripts/generate_ui_framework_fixtures.py).
const routes = (extra: Route[] = []): Route[] => [
  ['GET', /\/api\/auth\/session$/, screen.slip_session],
  ['GET', /\/api\/schema$/, screen.slip_schema],
  ['GET', /\/api\/customer\/0000000000001$/, screen.slip_customer],
  ['POST', /\/api\/_slips\/plan_visit\/present$/, screen.slip_present],
  ['POST', /\/api\/_slips\/plan_visit\/check$/, screen.slip_check],
  ['POST', /\/api\/_slips\/quick_customer\/present$/, screen.slip_quick_present],
  ['POST', /\/api\/_slips\/quick_customer\/hand-in$/, screen.slip_quick_hand_in],
  ...extra,
];
const sheet = () => document.querySelector<HTMLElement>('.modal.slip');
const field = (name: string) => sheet()!.querySelector<HTMLElement>(`.fld[data-field="${name}"]`);

async function openVisit(extra: Route[] = []) {
  const started = start(routes(extra), '/app/customer/0000000000001');
  await until(() => expect(byText(started.host, '.doc-head button', 'Plan a visit')).not.toBeNull());
  click(byText(started.host, '.doc-head button', 'Plan a visit'));
  await until(() => expect(sheet()).not.toBeNull());
  return started;
}

describe('slips', () => {
  it('presents a slip from its record with the server schema, layout and conditions', async () => {
    const { sent } = await openVisit();
    const present = requests(sent, 'POST', /plan_visit\/present$/)[0];
    expect(present.body.source).toEqual({ model: 'customer', id: id(1) });
    expect(text(sheet()!.querySelector('.modal-h h2'))).toBe('Plan a visit · Corniche Foods');
    expect([...sheet()!.querySelectorAll('.slip-group')].map(text)).toEqual(['Visit', 'Stops']);
    // The source fills the customer and keeps it read only; the rate applies only to installs.
    expect(field('customer')!.querySelector('input,select,button[aria-haspopup]:not([disabled])')).toBeNull();
    expect(field('rate')!.hidden).toBe(true);
    expect(field('hours')!.querySelector('.fval.ro')).not.toBeNull();
    expect(text(sheet()!.querySelector('.slip-empty'))).toBe('No rows yet.');
    expect(text(sheet()!.querySelector('.modal-f'))).toContain('Nothing is saved until you hand this in.');
    expect(byText(sheet()!, '.modal-f button', 'Plan visit')).not.toBeNull();
  });

  it('checks each change on the server and shows what it decides', async () => {
    const { sent } = await openVisit();
    choose(field('kind')!.querySelector<HTMLElement>('button[aria-haspopup="listbox"]')!, 'install');
    await until(() => expect(field('rate')!.hidden).toBe(false));
    const check = requests(sent, 'POST', /plan_visit\/check$/).at(-1)!;
    expect(check.body.values.kind).toBe('install');
    expect(check.body.values).not.toHaveProperty('customer');
    expect(check.body.values).not.toHaveProperty('hours');
    expect(check.body.source).toEqual({ model: 'customer', id: id(1) });
    expect(text(field('rate')!.querySelector('label'))).toContain('*');
  });

  it('adds rows, shows server errors where they belong, then hands in once with the record revision', async () => {
    let handIns = 0;
    const { sent } = await openVisit([['POST', /plan_visit\/hand-in$/, () => (++handIns === 1 ? screen.slip_hand_in_invalid : screen.slip_hand_in)]]);
    click(byText(sheet()!, 'button', 'Add row'));
    await until(() => expect(sheet()!.querySelectorAll('.slip-table tbody tr')).toHaveLength(1));
    // The recorded answer names row-1: the first row this page added.
    const key = sheet()!.querySelector<HTMLElement>('.slip-table tbody tr')!.dataset.key!;
    expect(key).toMatch(/^row-\d+$/);
    click(byText(sheet()!, '.modal-f button', 'Plan visit'));
    await until(() => expect(text(sheet()!.querySelector('.ev-error'))).toContain('Fix the marked fields'));
    expect(text(field('visit_on')!.querySelector('.err'))).toBe('This field is required');
    expect(text(sheet()!.querySelector('.slip-table td.invalid .err'))).toBe('This field is required');
    click(byText(sheet()!, '.modal-f button', 'Plan visit'));
    await until(() => expect(sheet()).toBeNull());
    expect(lastToast()).toBe('Visit planned for 2026-11-16');
    const sentHandIns = requests(sent, 'POST', /plan_visit\/hand-in$/);
    expect(sentHandIns).toHaveLength(2);
    expect(sentHandIns[1].headers['if-match']).toBe(screen.slip_present.body.source.revision);
    expect(sentHandIns[0].body.key).toBe(sentHandIns[1].body.key);
    expect(sentHandIns[1].body.values.children.stops[0]._key).toBe(key);
  });

  it('asks before setting aside a slip that has changes', async () => {
    await openVisit();
    press(sheet()!, 'Escape');
    await until(() => expect(sheet()).toBeNull());
    click(byText(document.body, '.doc-head button', 'Plan a visit'));
    await until(() => expect(sheet()).not.toBeNull());
    click(byText(sheet()!, 'button', 'Add row'));
    press(sheet()!, 'Escape');
    await until(() => expect(text(document.querySelectorAll('.modal-h h2')[1])).toBe('Discard this slip?'));
    expect(sheet()).not.toBeNull();
    click(byText(document.body, '.modal-f button', 'Discard'));
    await until(() => expect(document.querySelector('.modal')).toBeNull());
  });

  it('presents the slip an action answers with', async () => {
    // An action that returns present(PlanVisit, ...) in Python: its _outcome as tests/test_slips.py checks it.
    const schema = copy(screen.slip_schema);
    schema.body.models.find((model: any) => model.name === 'customer').actions = [{ name: 'plan', label: 'Plan' }];
    const answer = { status: 200, etag: screen.slip_customer.etag, body: { ...copy(screen.slip_customer.body),
      _outcome: { present: { slip: 'plan_visit', source: { model: 'customer', id: id(1) }, values: { kind: 'install' } } } } };
    const { host, sent } = start([['GET', /\/api\/schema$/, schema], ['POST', /\/api\/customer\/0000000000001\/actions\/plan$/, answer], ...routes()], '/app/customer/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Plan')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Plan'));
    await until(() => expect(sheet()).not.toBeNull());
    expect(requests(sent, 'POST', /plan_visit\/present$/)[0].body).toEqual({ source: { model: 'customer', id: id(1) }, values: { kind: 'install' } });
  });

  it('opens a New menu slip and turns it into a new record to review, saving nothing', async () => {
    const { host, router, sent } = start(routes(), '/app/customer/0000000000001');
    await until(() => expect(byText(host, '.top-actions button', 'New')).not.toBeNull());
    click(byText(host, '.top-actions button', 'New'));
    await until(() => expect(byText(document.body, '[role="menuitem"]', 'Quick customer')).not.toBeNull());
    click(byText(document.body, '[role="menuitem"]', 'Quick customer'));
    await until(() => expect(sheet()).not.toBeNull());
    click(byText(sheet()!, '.modal-f button', 'Quick customer'));
    await until(() => expect(requests(sent, 'POST', /quick_customer\/hand-in$/)).toHaveLength(1));
    await until(() => expect(router.route.peek()?.name).toBe('create'));
    await until(() => expect(text(host.querySelector('.ev-record'))).toContain('Filled in from Quick customer.'));
    expect(requests(sent, 'POST', /\/api\/customer$/)).toHaveLength(0);
    const name = host.querySelector<HTMLInputElement>('[data-field="name"] input')!;
    expect(name.value).toBe('Oasis Water');
    expect(host.querySelector<HTMLInputElement>('[data-field="credit_days"] input')!.value).toBe('45');
  });
});
