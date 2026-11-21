import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, lastToast, press, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';
import { choose } from './ui-helpers';

afterEach(() => stopAll());

// Responses recorded from the real routes (record_prompts in scripts/generate_ui_framework_fixtures.py).
// A test's own routes come first, so they can replace a recorded one.
const routes = (extra: Route[] = []): Route[] => [
  ...extra,
  ['GET', /\/api\/auth\/session$/, screen.prompt_session],
  ['GET', /\/api\/schema$/, screen.prompt_schema],
  ['GET', /\/api\/account\/0000000000001$/, screen.prompt_record],
  ['POST', /\/api\/account\/0000000000001\/actions\/plan_visit$/, screen.prompt_open],
];
const sheet = () => document.querySelector<HTMLElement>('.modal.prompt');
const field = (name: string) => sheet()!.querySelector<HTMLElement>(`[data-field="${name}"]`);

async function openVisit(extra: Route[] = []) {
  const started = start(routes(extra), '/app/account/0000000000001');
  await until(() => expect(byText(started.host, '.doc-head button', 'Plan a visit')).not.toBeNull());
  click(byText(started.host, '.doc-head button', 'Plan a visit'));
  await until(() => expect(sheet()).not.toBeNull());
  return started;
}

describe('prompts', () => {
  it('open from an action with their title, primary button and layout, and save nothing', async () => {
    const { sent } = await openVisit();
    expect(text(sheet()!.querySelector('.modal-h h2'))).toBe('Plan a visit · Corniche Foods');
    expect(byText(sheet()!, '.modal-f button', 'Plan visit')).not.toBeNull();
    expect(text(sheet()!.querySelector('.modal-f'))).toContain('Nothing is saved until you submit this.');
    // The source fills the account and keeps it read only; the rate applies only to installs.
    expect(field('account')!.querySelector('input:not([disabled]),button.in-link:not([disabled])')).toBeNull();
    expect(field('rate')!.hidden).toBe(true);
    expect(sheet()!.querySelector('.prompt-table')).not.toBeNull();
    expect(requests(sent, 'POST', /_prompts\//)).toHaveLength(0);
  });

  it('runs the rules in the browser, with no server check', async () => {
    const { sent } = await openVisit();
    choose(field('kind')!.querySelector<HTMLElement>('button[aria-haspopup="listbox"]')!, 'install');
    await until(() => expect(field('rate')!.hidden).toBe(false));
    expect(requests(sent, 'POST', /_prompts\/.*\/check$/)).toHaveLength(0);
  });

  it('shows server errors where they belong, then submits through its ticket and follows the outcome', async () => {
    let submits = 0;
    const { sent } = await openVisit([['POST', /\/api\/_prompts\/fixture-prompt-ticket\/submit$/,
      () => (++submits === 1 ? screen.prompt_submit_invalid : screen.prompt_submit)]]);
    // A required field the cursor is in is checked in place first; out of it, the server decides.
    (document.activeElement as HTMLElement | null)?.blur();
    click(byText(sheet()!, '.modal-f button', 'Plan visit'));
    await until(() => expect(text(field('visit_on')!.querySelector('.err'))).toBe('This field is required'));
    expect(submits).toBe(1);
    click(byText(sheet()!, '.modal-f button', 'Plan visit'));
    await until(() => expect(sheet()).toBeNull());
    expect(lastToast()).toBe('Visit planned for 2026-11-16');
    const sentSubmits = requests(sent, 'POST', /_prompts\/fixture-prompt-ticket\/submit$/);
    expect(sentSubmits).toHaveLength(2);
    // Only what people fill in is sent: not the read-only account, nor the calculated hours.
    expect(sentSubmits[1].body.values).not.toHaveProperty('account');
    expect(sentSubmits[1].body.values).not.toHaveProperty('hours');
    expect(sentSubmits[1].body.values.children).toEqual({ stops: [] });
  });

  it('offers no way to add a row to a table load() filled', async () => {
    // As the server answers when load() fills the stops: their rows, and max_rows at their count.
    const body = copy(screen.prompt_open.body);
    body._outcome.prompt.values.children = { stops: [{ _key: 'k1', place: 'Dock', hours: '1.0' }, { _key: 'k2', place: 'Yard', hours: '2.0' }] };
    body._outcome.prompt.models[0].children[0].max_rows = 2;
    await openVisit([['POST', /\/api\/account\/0000000000001\/actions\/plan_visit$/, { ...screen.prompt_open, body }]]);
    const rows = () => sheet()!.querySelectorAll('.prompt-table tbody tr[data-key]').length;
    await until(() => expect(rows()).toBe(2));
    expect(byText(sheet()!, '.prompt-table button', /Add row/)).toBeNull();
    // Enter on the last cell after typing does not add a row either.
    const last = [...sheet()!.querySelectorAll<HTMLElement>('.prompt-table td[data-col="hours"]')].pop()!;
    click(last);
    const input = last.querySelector('input');
    if (input) { input.value = '3'; input.dispatchEvent(new Event('input', { bubbles: true })); press(input, 'Enter'); }
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(rows()).toBe(2);
    // Nor does Duplicate on a selected row: a copy is a new row.
    click(sheet()!.querySelector<HTMLElement>('.prompt-table tbody tr[data-key] [role="checkbox"]'));
    await until(() => expect(byText(sheet()!, '.prompt-table button', 'Duplicate')).not.toBeNull());
    click(byText(sheet()!, '.prompt-table button', 'Duplicate'));
    expect(rows()).toBe(2);
  });
});
