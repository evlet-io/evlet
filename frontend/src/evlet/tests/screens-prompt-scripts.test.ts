// A model script in a prompt (Meta.script on an evlet.Prompt): the same hooks as on a record, over the prompt's
// RecordStore, on its fields and its Table rows (the grid and the row editor). Recorded responses from
// record_prompts (screens-fixture.json), with a script and hooks added to the prompt's open response.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { byText, click, screen, start, stopAll, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';
import { choose } from './ui-helpers';
import { evlet, setScriptImporter } from '../model/script';
import { showTip, tipText } from '../ui/tooltip';

afterEach(() => { vi.restoreAllMocks(); stopAll(); });

const URL_ = '/assets/scripts/0123456789abcdef/plan_visit.js';
const log: unknown[][] = [];
let instance: any = null;

class PlanVisit extends evlet.Model {
  opened() {
    instance = this;
    log.push(['opened', this.model, this.id, this.is_new, this.state]);
    try { this.field('account').set('0000000000002'); } catch (error) { log.push(['refused', (error as Error).message]); }
    for (const name of ['call', 'move', 'save', 'reload'] as const) {
      (this[name] as (...args: unknown[]) => Promise<unknown>)('x').then(() => log.push([name, 'resolved']), error => log.push([name, (error as Error).message]));
    }
  }
  kind_changed(field: any) { log.push(['kind', field.value]); }
  visit_tip() { return { title: 'Visit', lines: ['Pick a weekday'] }; }
  hours_look() { return { tone: 'danger' }; }
  place_input(el: HTMLElement, field: any, row: any) {
    const input = document.createElement('input');
    input.value = field.value ?? '';
    input.addEventListener('change', () => field.set(input.value));
    el.append(input);
    log.push(['control', row?.index ?? null]);
  }
}

function opened(scripted: boolean): Recorded {
  const body = copy(screen.prompt_open.body);
  const [prompt, stops] = body._outcome.prompt.models;
  if (scripted) {
    prompt.script = { url: URL_, on_open: 'opened' };
    prompt.fields.find((item: any) => item.name === 'visit_on').script = { tooltip: 'visit_tip' };
    prompt.fields.find((item: any) => item.name === 'kind').script = { on_change: 'kind_changed' };
    stops.fields.find((item: any) => item.name === 'place').script = { control: 'place_input' };
    stops.fields.find((item: any) => item.name === 'hours').script = { look: 'hours_look' };
  }
  return { ...screen.prompt_open, body };
}
const routes = (scripted: boolean): Route[] => [
  ['GET', /\/api\/auth\/session$/, screen.prompt_session],
  ['GET', /\/api\/schema$/, screen.prompt_schema],
  ['GET', /\/api\/account\/0000000000001$/, screen.prompt_record],
  ['POST', /\/api\/account\/0000000000001\/actions\/plan_visit$/, opened(scripted)],
];
const sheet = () => document.querySelector<HTMLElement>('.modal.prompt');
const field = (name: string) => sheet()!.querySelector<HTMLElement>(`[data-field="${name}"]`);

async function open(scripted = true) {
  log.length = 0;
  instance = null;
  const load = vi.fn(async () => ({ default: PlanVisit }));
  setScriptImporter(load);
  const started = start(routes(scripted), '/app/account/0000000000001');
  await until(() => expect(byText(started.host, '.doc-head button', 'Plan a visit')).not.toBeNull());
  click(byText(started.host, '.doc-head button', 'Plan a visit'));
  await until(() => expect(sheet()).not.toBeNull());
  return { ...started, load };
}

describe('a model script in a prompt', () => {
  it('runs on_open once after load(), as a new form with no saved record', async () => {
    const { load } = await open();
    await until(() => expect(log.filter(entry => entry[0] === 'reload')).toHaveLength(1));
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith(URL_);
    expect(log.filter(entry => entry[0] === 'opened')).toEqual([['opened', '_prompt.plan_visit', null, true, null]]);
    // set() refuses a read-only field; call, move, save and reload reject: a prompt has no saved record.
    expect(log).toContainEqual(['refused', '_prompt.plan_visit.account cannot be set: it is read only']);
    for (const name of ['call', 'move', 'save', 'reload']) expect(log).toContainEqual([name, expect.stringContaining('A prompt has no saved record')]);
  });

  it('runs on_change and the tooltip on the prompt\'s fields', async () => {
    await open();
    await until(() => expect(instance).not.toBeNull());
    choose(field('kind')!.querySelector<HTMLElement>('button[aria-haspopup="listbox"]')!, 'install');
    await until(() => expect(log).toContainEqual(['kind', 'install']));
    const tip = field('visit_on')!.querySelector<HTMLElement>('.fld-tip')!;
    showTip(tip, '');
    tip.dispatchEvent(new FocusEvent('focus'));
    expect(tipText()).toBe('Visit\nPick a weekday');
  });

  it('draws a control and a look on a Table row, in the grid and in the row editor, and keys stay in the control', async () => {
    await open();
    await until(() => expect(instance).not.toBeNull());
    click(byText(sheet()!, '.prompt-table button', /Add row/));
    const cell = () => sheet()!.querySelector<HTMLElement>('td[data-col="place"] .ev-script');
    await until(() => expect(cell()?.shadowRoot?.querySelector('input')).not.toBeNull());
    expect(log).toContainEqual(['control', 0]);
    expect(sheet()!.querySelector('td[data-col="hours"]')!.getAttribute('data-look')).toBe('danger');
    // Adding the row starts its first cell, a drawn one, in its own control rather than the grid's editor.
    const input = cell()!.shadowRoot!.querySelector('input')!;
    expect(cell()!.shadowRoot!.activeElement).toBe(input);
    expect(sheet()!.querySelector('td.editing')).toBeNull();
    // Two characters typed into the control's input both belong to it: the grid neither starts its own edit nor eats one.
    for (const key of ['2', '5']) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, composed: true, cancelable: true });
      input.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(sheet()!.querySelector('td.editing')).toBeNull();
    // Tab still moves on: the grid keeps it.
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, composed: true, cancelable: true });
    input.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    input.value = '25';
    input.dispatchEvent(new Event('change'));
    // The row editor draws the same control, from the same script.
    click(sheet()!.querySelector<HTMLElement>('.g-exp'));
    await until(() => expect(sheet()!.querySelector('.ev-row-form [data-field="place"] .ev-script, .g-ed [data-field="place"] .ev-script')).not.toBeNull());
    expect(log.filter(entry => entry[0] === 'control').length).toBeGreaterThanOrEqual(2);
  });

  it('goes inert when the modal closes, and a prompt without a script loads none', async () => {
    await open();
    await until(() => expect(instance).not.toBeNull());
    expect(instance.field('visit_on').editable).toBe(true);
    click(byText(sheet()!, '.modal-f button', 'Cancel'));
    await until(() => expect(sheet()).toBeNull());
    expect(instance.field('visit_on').value).toBe(undefined);
    stopAll();
    const { load } = await open(false);
    expect(load).not.toHaveBeenCalled();
    expect(sheet()!.querySelector('.ev-script')).toBeNull();
  });
});
