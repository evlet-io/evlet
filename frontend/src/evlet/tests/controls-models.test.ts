// Controls Models and Naming in jsdom: field drafts, the layout helpers, Add field, series checks
// from the server and Set counter, against a stub of the /api/_controls routes.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient, type LayoutSection } from '../controls/client';
import { moveField, nudge, slug } from '../controls/models';
import { server, type Sent } from './server';
import { byText, click, lastToast, stopAll, text, typeInto, until } from './screens-helpers';

const props = (label: string, extra: Record<string, unknown> = {}) =>
  ({ label, help_text: '', placeholder: '', hidden: false, required: false, read_only: false, default: null, options: null, ...extra });
const info = (name: string, type: string, extra: Record<string, unknown> = {}) =>
  ({ name, type, custom: false, editable: true, stored: true, unique: false, target: null, width: type === 'longtext' ? 12 : 6, ...extra });
const lead = { name: 'Lead', label: 'Lead', app: 'crm', scopes: [null], table: 'lead', records: 3, naming: null,
  tabs: [['activity', 'Activity'], ['details', 'Details'], ['revisions', 'Revisions'], ['lines', 'Lines'], ['related', 'Related'], ['postings', 'Postings']].map(([name, label]) => ({ name, label })),
  fields: [], fields_detail: [info('title', 'text'), info('source', 'select'), info('value', 'decimal'), info('notes', 'longtext')] };
const invoice = { name: 'Invoice', label: 'Invoice', app: 'billing', scopes: [null], table: 'invoice', records: 2, naming: { field: 'reference', date_field: null },
  fields: [], fields_detail: [info('reference', 'text', { editable: false, unique: true }), info('total', 'decimal'), info('status', 'select')] };
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [],
  roles: [{ id: 'admin', kind: 'framework', base: null, realm: 'desk', opens_controls: true, description: '', users: 1, app: null, declared: true }],
  models: [lead, invoice], scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
  users: { active: 1, total: 1 }, audit: [], two_step: false, links: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
});
const series = [{ id: 'zero', label: 'Zero-rated', format: 'ZR-{YYYY}-{####}', when: 'total == 0' }, { id: 'default', label: 'Invoices', format: 'INV-{YYYY}-{####}', when: '' }];
const item = (kind: string, key: string, file: unknown) => ({ kind, key, file, value: null, changed_by: null, changed_at: null });
const overrides = () => ({ version: 1, problems: [], items: [
  item('field', 'Lead.title', props('Title', { required: true })), item('field', 'Lead.source', props('Source', { options: ['web', 'walk_in'] })),
  item('field', 'Lead.value', props('Value')), item('field', 'Lead.notes', props('Notes')),
  item('layout', 'Lead', [{ title: 'Details', fields: [{ name: 'title', width: 6 }, { name: 'source', width: 6 }, { name: 'value', width: 6 }, { name: 'notes', width: 12 }] }]),
  item('list', 'Lead', { columns: ['title', 'source'], page_size: 50, sort: '-id' }),
  item('field', 'Invoice.reference', props('Reference', { read_only: true })), item('field', 'Invoice.total', props('Total')),
  item('field', 'Invoice.status', props('Status', { options: ['draft', 'sent'] })),
  item('layout', 'Invoice', [{ title: 'Details', fields: [] }]), item('list', 'Invoice', { columns: ['reference'], page_size: 50, sort: '-id' }),
  item('naming', 'Invoice', series),
] });
const counters = [{ prefix: 'INV-2026-', value: 142, last_name: 'INV-2026-0142', how: 'issued', note: null, changed_by: 'fatima', changed_at: 1790000000,
  series: 'default', next: 'INV-2026-0143', issued: 142 }];

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

function open(path: string) {
  window.history.replaceState(null, '', path);
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
    ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(), etag: null })],
    ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: overrides(), etag: null })],
    ['POST', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: { version: 2, saved: 1 }, etag: null })],
    ['POST', /\/api\/_controls\/fields\/Lead$/, () => ({ status: 201, body: { name: 'x_lpo_number', backup: '20261102T000000Z.sqlite3', version: 2 }, etag: null })],
    ['GET', /\/api\/_controls\/naming\/Invoice\/counters$/, () => ({ status: 200, body: { field: 'reference', items: counters }, etag: null })],
    ['POST', /\/api\/_controls\/naming\/Invoice\/counters$/, () => ({ status: 200, body: { field: 'reference', items: counters }, etag: null })],
    ['POST', /\/api\/_controls\/naming\/Invoice\/try$/, (sent: Sent) => {
      const draft = sent.body.series as typeof series | undefined;
      const bad = draft?.find(entry => entry.when.includes('totl'));
      return { status: 200, etag: null, body: bad
        ? { checks: draft!.map(entry => entry === bad ? { id: entry.id, when: "totl isn't a field on this model. Did you mean total?" } : { id: entry.id, next: 'P-001' }), match: 'probe_last', label: 'Last', name: 'P-001', error: null }
        : { checks: (draft ?? series).map(entry => ({ id: entry.id, next: `${entry.format.split('{')[0]}2026-0143` })), match: 'default', label: 'Invoices', name: 'INV-2026-0143', error: null } };
    }],
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent };
}
const bar = () => document.querySelector('.ctl-changebar');

describe('layout helpers', () => {
  const layout = (): LayoutSection[] => [{ title: 'A', fields: [{ name: 'a', width: 6 }, { name: 'b', width: 6 }] }, { title: 'B', fields: [{ name: 'c', width: 12 }] }];
  it('move fields between sections and across section edges', () => {
    expect(moveField(layout(), 'c', 0, 'b')[0].fields.map(item => item.name)).toEqual(['a', 'c', 'b']);
    expect(moveField(layout(), 'a', 1, null)[1].fields.map(item => item.name)).toEqual(['c', 'a']);
    expect(nudge(layout(), 'b', 1).map(section => section.fields.map(item => item.name))).toEqual([['a'], ['b', 'c']]);
    expect(nudge(layout(), 'c', -1).map(section => section.fields.map(item => item.name))).toEqual([['a', 'b', 'c'], []]);
    expect(slug('LPO number')).toBe('lpo_number');
  });
});

describe('Models', () => {
  it('turns a field toggle into a field override draft and saves it', async () => {
    const { host, sent } = open('/controls/models?model=Lead');
    await until(() => expect(host.querySelector('[role="switch"][aria-label="Required value"]')).toBeTruthy());
    expect(host.querySelector('[aria-label="Required title"]')).toBeNull();
    click(host.querySelector('[role="switch"][aria-label="Required value"]'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Review'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Lead.value'));
    expect(text(document.querySelector('.modal .ctl-to'))).toBe('Required');
    click(byText(document.body, '.modal button', 'Save changes'));
    await until(() => expect(bar()).toBeNull());
    expect(sent.filter(entry => entry.method === 'POST')[0].body).toEqual({ changes: [{ kind: 'field', key: 'Lead.value', value: { required: true } }] });
  });

  it('hides an option in the field drawer and keeps one shown', async () => {
    const { host } = open('/controls/models?model=Lead');
    await until(() => expect(byText(host, 'tr', /^source/)).toBeTruthy());
    click(byText(host, 'tr', /^source/));
    await until(() => expect(document.querySelector('.drawer [aria-label="Show walk_in"]')).toBeTruthy());
    click(document.querySelector('.drawer [aria-label="Show walk_in"]'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(document.querySelector('.drawer [aria-label="Show web"]'));
    await until(() => expect(lastToast()).toBe('Keep at least one option shown'));
  });

  it('adds an instance field at once', async () => {
    const { host, sent } = open('/controls/models?model=Lead');
    await until(() => expect(byText(host, 'button', 'Add field')).toBeTruthy());
    click(byText(host, 'button', 'Add field'));
    await until(() => expect(document.querySelector('#ctl-nf-label')).toBeTruthy());
    typeInto(document.querySelector<HTMLInputElement>('#ctl-nf-label')!, 'LPO number');
    await until(() => expect(document.querySelector<HTMLInputElement>('#ctl-nf-name')!.value).toBe('lpo_number'));
    click(byText(document.body, '.modal button', 'Add field'));
    await until(() => expect(lastToast()).toBe('x_lpo_number added. Backup 20261102T000000Z.sqlite3 saved first.'));
    expect(sent.find(entry => entry.method === 'POST' && entry.url.endsWith('/fields/Lead'))!.body)
      .toEqual({ name: 'lpo_number', type: 'Text', label: 'LPO number', help_text: '', required: false });
  });

  it('moves a field in the layout editor', async () => {
    const { host, sent } = open('/controls/models?model=Lead&tab=layout');
    await until(() => expect(host.querySelector('.ctl-lf[data-field="value"]')).toBeTruthy());
    click(host.querySelector('.ctl-lf[data-field="value"] [aria-label="Move earlier"]'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(bar()).toBeNull());
    const posted = sent.filter(entry => entry.method === 'POST')[0].body.changes[0];
    expect(posted.kind).toBe('layout');
    expect(posted.value[0].fields.map((entry: { name: string }) => entry.name)).toEqual(['title', 'value', 'source', 'notes']);
  });
});

describe('collapsible sections in the layout editor', () => {
  it('sets Can collapse and Starts per section, and turning collapse off drops both', async () => {
    const { host, sent } = open('/controls/models?model=Lead&tab=layout');
    await until(() => expect(host.querySelector('.ctl-lsec-fold')).toBeTruthy());
    const starts = () => host.querySelector<HTMLButtonElement>('#ctl-ls-starts-0')!;
    expect(starts().disabled).toBe(true);
    const fold = () => byText(host, '.ctl-lsec-fold [role="switch"]', 'Can collapse');
    click(fold());
    await until(() => expect(starts().disabled).toBe(false));
    click(starts());
    click(byText(document.body, '[role="option"]', 'Closed'));
    await until(() => expect(text(starts())).toBe('Closed'));
    // Turning collapse off drops both options, so it starts open again when turned back on.
    click(fold());
    await until(() => expect(starts().disabled).toBe(true));
    expect(text(starts())).toBe('Open');
    click(fold());
    await until(() => expect(starts().disabled).toBe(false));
    expect(text(starts())).toBe('Open');
    click(starts());
    click(byText(document.body, '[role="option"]', 'Closed when empty'));
    await until(() => expect(text(starts())).toBe('Closed when empty'));
    expect(text(host.querySelector('.ctl-pvsec h3'))).toContain('closed when empty');
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(bar()).toBeNull());
    const posted = sent.filter(entry => entry.method === 'POST')[0].body.changes[0];
    expect(posted.value[0]).toMatchObject({ title: 'Details', collapsible: true, collapsed: 'empty' });
  });
});

describe('order in the layout editor', () => {
  const key = (target: Element, name: string) => target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
  const grip = (host: Element, selector: string) => host.querySelector<HTMLElement>(`${selector} [data-drag-handle]`)!;

  it('loads a layout saved as a plain list as rows, and saves flow, field and section order, tab order and the opening tab', async () => {
    const { host, sent } = open('/controls/models?model=Lead&tab=layout');
    await until(() => expect(host.querySelector('.ctl-lsec-fold')).toBeTruthy());
    expect(text(host.querySelector('#ctl-ls-flow-0'))).toBe('Across, then down');
    click(host.querySelector('#ctl-ls-flow-0'));
    click(byText(document.body, '[role="option"]', 'Down each column'));
    await until(() => expect(host.querySelector('.ctl-pvsec .fs-cols')).not.toBeNull());

    // Keyboard: lift notes, move it up past value, drop.
    grip(host, '[data-drag-id="notes"]').focus();
    key(grip(host, '[data-drag-id="notes"]'), ' ');
    key(grip(host, '[data-drag-id="notes"]'), 'ArrowUp');
    key(grip(host, '[data-drag-id="notes"]'), ' ');
    await until(() => expect([...host.querySelectorAll<HTMLElement>('.ctl-lf')].map(item => item.dataset.field)).toEqual(['title', 'source', 'notes', 'value']));
    expect(text(host.querySelector('[data-drag-live="field"]'))).toContain('Notes dropped');

    // A second section, then move it above the first by keyboard.
    click(byText(host, 'button', 'Add section'));
    await until(() => expect(host.querySelectorAll('.ctl-lsec')).toHaveLength(2));
    const second = () => grip(host, '.ctl-lsec[data-drag-id="Section 2"]');
    second().focus();
    key(second(), ' ');
    key(second(), 'ArrowUp');
    key(second(), ' ');
    await until(() => expect([...host.querySelectorAll<HTMLInputElement>('.ctl-lsec-head input')].map(input => input.value)).toEqual(['Section 2', 'Details']));

    // Record tabs: Lines first, and records open on it.
    const lines = () => grip(host, '.ctl-tabrow[data-drag-id="lines"]');
    lines().focus();
    for (const step of [' ', 'ArrowUp', 'ArrowUp', 'ArrowUp', ' ']) key(lines(), step);
    await until(() => expect([...host.querySelectorAll<HTMLElement>('.ctl-tabrow')].map(row => row.dataset.dragId)).toEqual(['lines', 'activity', 'details', 'revisions', 'related', 'postings']));
    click(host.querySelector('#ctl-tabs-home'));
    click(byText(document.body, '[role="option"]', 'Lines'));
    await until(() => expect(text(byText(host, '.ctl-tabrow', /^Lines/))).toContain('Opens first'));

    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(bar()).toBeNull());
    const posted = sent.filter(entry => entry.method === 'POST')[0].body.changes[0].value;
    expect(posted.tabs).toEqual(['lines', 'activity', 'details', 'revisions', 'related', 'postings']);
    expect(posted.default_tab).toBe('lines');
    expect(posted.sections.map((section: { title: string }) => section.title)).toEqual(['Section 2', 'Details']);
    expect(posted.sections[1]).toMatchObject({ flow: 'columns', fields: [{ name: 'title', width: 6 }, { name: 'source', width: 6 }, { name: 'notes', width: 12 }, { name: 'value', width: 6 }] });
  });
});

describe('Naming', () => {
  it('shows series from the server, the condition error and the next names', async () => {
    const { host } = open('/controls/naming');
    await until(() => expect(text(host.querySelector('.ctl-ser.hit'))).toContain('Matches the test record'));
    expect(text(host.querySelector('.ctl-tryres'))).toContain('INV-2026-0143 by Invoices');
    click(byText(host, '.ctl-ser', /Zero-rated/)!.querySelector('button:last-child'));
    await until(() => expect(document.querySelector<HTMLTextAreaElement>('#ctl-s-when')).toBeTruthy());
    typeInto(document.querySelector<HTMLTextAreaElement>('#ctl-s-when')!, 'totl == 0');
    await until(() => expect(text(document.querySelector('.drawer'))).toContain("totl isn't a field on this model. Did you mean total?"));
    expect(byText<HTMLButtonElement>(document.body, '.drawer button', 'Apply')!.disabled).toBe(true);
  });

  it('refuses a counter below the last issued name and saves one with a reason', async () => {
    const { host, sent } = open('/controls/naming');
    await until(() => expect(byText(host, 'button', 'Set counter')).toBeTruthy());
    click(byText(host, 'button', 'Set counter'));
    await until(() => expect(document.querySelector('#ctl-c-value')).toBeTruthy());
    typeInto(document.querySelector<HTMLInputElement>('#ctl-c-value')!, '100');
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Lower than INV-2026-0142, which already exists.'));
    typeInto(document.querySelector<HTMLInputElement>('#ctl-c-value')!, '500');
    typeInto(document.querySelector<HTMLInputElement>('#ctl-c-note')!, 'Continues from the old system');
    click(byText(document.body, '.modal button', 'Set counter'));
    await until(() => expect(lastToast()).toBe('Counter set. Next name is INV-2026-0501'));
    expect(sent.find(entry => entry.method === 'POST' && entry.url.endsWith('/counters'))!.body)
      .toEqual({ prefix: 'INV-2026-', value: 500, note: 'Continues from the old system' });
  });
});
