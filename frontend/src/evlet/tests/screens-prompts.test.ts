import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, lastToast, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { choose } from './ui-helpers';

afterEach(() => stopAll());

// Responses recorded from the real routes (record_prompts in scripts/generate_ui_framework_fixtures.py).
const routes = (extra: Route[] = []): Route[] => [
  ['GET', /\/api\/auth\/session$/, screen.prompt_session],
  ['GET', /\/api\/schema$/, screen.prompt_schema],
  ['GET', /\/api\/account\/0000000000001$/, screen.prompt_record],
  ['POST', /\/api\/account\/0000000000001\/actions\/plan_visit$/, screen.prompt_open],
  ...extra,
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
});
