// Docs, State display: live examples drawn with the record page's own State pieces over sample schemas.
import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, start, stopAll, text, until } from './screens-helpers';

afterEach(stopAll);

const EXAMPLES = ['badge', 'bar', 'none', 'colours', 'locked', 'version', 'approval', 'primary', 'dialog'];

async function open() {
  const { host } = start(adminRoutes(), '/docs/states');
  await until(() => expect(text(host.querySelector('.ph h1'))).toBe('State display'));
  const example = (name: string) => host.querySelector<HTMLElement>(`[data-example="${name}"]`)!;
  return { host, example };
}
const mainLabel = (root: Element) => text(root.querySelector('.split-main'));

describe('Docs, State display', () => {
  it('is a Docs topic with every example, each next to its Python', async () => {
    const { host, example } = await open();
    expect(text(byText(host, '.ev-docs .tabs a', 'State display'))).toBe('State display');
    for (const name of EXAMPLES) expect(example(name), name).not.toBeNull();
    const python = [...host.querySelectorAll('.ev-code pre')].map(node => text(node));
    expect(python.some(code => code.includes('show="badge"'))).toBe(true);
    expect(python.some(code => code.includes('show="bar"'))).toBe(true);
    expect(python.some(code => code.includes('show="none"'))).toBe(true);
    expect(python.some(code => code.includes('levels=(Level(by=("manager",)'))).toBe(true);
  });

  it('is linked from the overview', async () => {
    const { host } = start(adminRoutes(), '/docs');
    await until(() => expect(byText(host, '.ev-doc-link', /^State display/)).not.toBeNull());
    click(byText(host, '.ev-doc-link', /^State display/));
    await until(() => expect(location.pathname).toBe('/docs/states'));
  });

  it('show="badge": the coloured badge offers the moves and moves the example', async () => {
    const { example } = await open();
    const badge = example('badge').querySelector<HTMLButtonElement>('.ev-status-go')!;
    expect(badge.className).toContain('b-blue');
    expect(badge.getAttribute('aria-label')).toBe('Status: Qualified. Change status');
    expect(mainLabel(example('badge'))).toBe('Send proposal');
    click(badge);
    await until(() => expect(byText(document, '[role="menuitem"]', 'Send proposal')).not.toBeNull());
    expect(byText(document, '[role="menuitem"]', 'Mark lost')).not.toBeNull();
    click(byText(document, '[role="menuitem"]', 'Send proposal'));
    await until(() => expect(text(example('badge').querySelector('.ev-status'))).toBe('Proposal'));
    expect(example('badge').querySelector('.ev-status')!.className).toContain('b-violet');
  });

  it('show="bar": every step, the current marked, final steps green and rose, only allowed moves are buttons', async () => {
    const { example } = await open();
    const bar = example('bar').querySelector('.stepper')!;
    expect(bar.getAttribute('aria-label')).toBe('Deal status');
    const steps = [...bar.querySelectorAll('.step')];
    expect(steps.map(step => text(step))).toEqual(['Lead', 'Qualified', 'Proposal', 'Negotiation', 'Won', 'Lost']);
    expect(bar.querySelector('[aria-current="step"]')!.textContent).toContain('Proposal');
    expect(steps.filter(step => step.classList.contains('done')).length).toBe(2);
    expect(steps[4].classList.contains('won')).toBe(true);
    expect(steps[5].classList.contains('lost')).toBe(true);
    expect([...bar.querySelectorAll('.step-go')].map(button => text(button))).toEqual(['Negotiation', 'Lost']);
    click(bar.querySelector('.step-go'));
    await until(() => expect(example('bar').querySelector('[aria-current="step"]')!.textContent).toContain('Negotiation'));
  });

  it('show="none": no badge, the header keeps its moves', async () => {
    const { example } = await open();
    expect(example('none').querySelector('.ev-status')).toBeNull();
    expect(mainLabel(example('none'))).toBe('Send proposal');
  });

  it('step colours, the draft pill, locked and cancelled final steps', async () => {
    const { example } = await open();
    const looks = [...example('colours').querySelectorAll('.ev-status')].map(node => [text(node), node.className]);
    expect(looks.map(([label]) => label)).toEqual(['Lead', 'Qualified', 'Proposal', 'Negotiation', 'Won', 'Lost', 'Draft', 'Issued', 'Paid', 'Void']);
    const cls = (label: string) => looks.find(([name]) => name === label)![1];
    expect(cls('Lead')).toContain('ev-status-draft');
    expect(cls('Qualified')).toContain('b-blue');
    expect(cls('Negotiation')).toContain('b-amber');
    expect(cls('Won')).toContain('ev-status-locked');
    expect(cls('Lost')).toContain('ev-status-cancelled');
    expect(cls('Issued')).toContain('ev-status-locked');
    expect(cls('Void')).toContain('ev-status-cancelled');
  });

  it('locked and released steps, and the version marker', async () => {
    const { example } = await open();
    expect([...example('locked').querySelectorAll('.inv-banner')].map(node => text(node))).toEqual([
      'Draft. Everything can change until it is issued.', 'Issued and locked. Only remarks can change.',
      'Void on 13-11-2026 by Sam Lee. Reason: Sent twice. Only remarks can change.']);
    const markers = [...example('version').querySelectorAll('[aria-label^="Version"]')];
    expect(markers.map(node => node.getAttribute('aria-label'))).toEqual(['Version: v2 released', 'Version: v2, changed since', 'Version: Not released yet']);
    expect(markers.map(node => node.className)).toEqual(['pill ev-status b-green', 'pill ev-status b-amber', 'pill ev-status b-slate']);
  });

  it('a step waiting for approval: level, pending text, and who may approve', async () => {
    const { example } = await open();
    const rows = [...example('approval').querySelectorAll('.ev-doc-example-row')];
    expect(rows.map(row => row.querySelector('[aria-label^="Approval"]')!.getAttribute('aria-label'))).toEqual(['Approval: Pending manager approval · level 1', 'Approval: Pending manager approval · level 1']);
    const [manager, submitter] = rows.map(row => row.querySelector('.split-main')!);
    expect(text(manager)).toBe('Approve');
    expect(manager.hasAttribute('data-blocked')).toBe(false);
    expect(text(submitter)).toBe('Approve');
    expect(submitter.getAttribute('aria-disabled')).toBe('true');
    click(submitter);
    expect(lastToast()).toBe('Waiting for another approver');
  });

  it('the primary move against the others, and a blocked move with its reason', async () => {
    const { example } = await open();
    const rows = [...example('primary').querySelectorAll('.ev-doc-example-row')];
    expect(rows.map(row => mainLabel(row))).toEqual(['Mark won', 'Revise proposal', 'Mark won']);
    const blocked = rows[2].querySelector('.split-main')!;
    expect(blocked.hasAttribute('data-blocked')).toBe(true);
    expect(text(rows[2])).toContain('The condition for this move is not met');
    click(rows[0].querySelector('.split-more'));
    await until(() => expect([...document.querySelectorAll('[role="menuitem"]')].map(node => text(node))).toEqual(['Revise proposal', 'Mark lost']));
  });

  it('the move dialog asks for a required remark and the asked field', async () => {
    const { example } = await open();
    click(byText(example('dialog'), 'button', 'Try Mark lost'));
    await until(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(text(dialog)).toContain('Why was it lost?');
    expect(text(dialog)).toContain('Competitor');
    click(byText(dialog, '.modal-f button', 'Mark lost'));
    await until(() => expect(text(dialog)).toContain('Why was it lost? is required'));
  });
});
