// Controls in jsdom: the change bar flow (draft, Review, Discard, Save, per-item errors) and the
// instant saves of instance data, against a stub of the /api/_controls routes.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient, type OverrideItem, type PermValue } from '../controls/client';
import { normalize, toggleOp } from '../controls/access';
import { ipAllowed } from '../controls/drafts';
import { server, type Sent } from './server';
import { byText, click, stopAll, text, until, lastToast } from './screens-helpers';

const all: PermValue = { ops: ['read', 'create', 'update', 'delete'], read: null, write: null, scope: null };
const roles = [
  { id: 'admin', kind: 'framework', base: null, realm: 'desk', opens_controls: true, description: 'Full access to every app. Opens Controls.', users: 1, app: null, declared: true },
  { id: 'editor', kind: 'framework', base: null, realm: 'desk', opens_controls: false, description: 'Reads and changes records.', users: 2, app: null, declared: true },
  { id: 'viewer', kind: 'framework', base: null, realm: 'desk', opens_controls: false, description: 'Reads records only.', users: 0, app: null, declared: true },
];
let me = { id: 1, username: 'owner', role: 'admin' };
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '0.1.3', version: 1 },
  me: { ...me },
  apps: [], roles,
  models: [{ name: 'Customer', label: 'Customer', app: null, scopes: [null, 'owner'],
    fields: [{ name: 'name', label: 'Name', read_only: false }, { name: 'email', label: 'Email', read_only: false }, { name: 'owner', label: 'Owner', read_only: false }] }],
  scopes: { '': 'All records', owner: 'Records they own' },
  settings: { 'security.password_min': { file: 15, value: 15 }, 'security.session_hours': { file: 8, value: 8 }, 'security.lockout': { file: 10, value: 10 },
    'security.two_step_roles': { file: ['admin'], value: ['admin'] }, 'security.controls_ips': { file: [], value: [] } },
  controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0, users: { active: 3, total: 3 }, audit: [], two_step: false, links: false, client: '127.0.0.1',
});
const item = (kind: 'perm' | 'setting', key: string, file: unknown, value: unknown = null): OverrideItem =>
  ({ kind, key, file, value, changed_by: value === null ? null : 'owner', changed_at: value === null ? null : 1790000000, source: 'framework' });
const overrides = (saved: Record<string, unknown> = {}) => ({ version: 1, problems: [], items: [
  ...['admin', 'editor'].map(role => item('perm', `${role}|Customer`, all, saved[`${role}|Customer`] ?? null)),
  item('perm', 'viewer|Customer', { ...all, ops: ['read'] }, saved['viewer|Customer'] ?? null),
  item('setting', 'security.password_min', 15), item('setting', 'security.session_hours', 8, saved['security.session_hours'] ?? null),
  item('setting', 'security.lockout', 10), item('setting', 'security.two_step_roles', ['admin']), item('setting', 'security.controls_ips', []),
] });

function open(path: string, save: (sent: Sent) => { status: number; body: any }) {
  window.history.replaceState(null, '', path);
  let saved: Record<string, unknown> = {};
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
    ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(), etag: null })],
    ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: overrides(saved), etag: null })],
    ['GET', /\/api\/_controls\/users/, () => ({ status: 200, etag: null, body: { offset: 0, limit: 100, total: 2, items: [
      { id: 1, username: 'owner', role: 'admin', active: true, sessions: 1 }, { id: 2, username: 'rahul', role: 'editor', active: true, sessions: 0 }] } })],
    ['POST', /\/api\/_controls\/overrides$/, (sent: Sent) => {
      const reply = save(sent);
      if (reply.status === 200) for (const change of sent.body.changes) saved = { ...saved, [change.key]: change.value };
      return { ...reply, etag: null };
    }],
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent, router };
}
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

const bar = () => document.querySelector('.ctl-changebar');
const checkbox = (host: Element, label: string) => host.querySelector<HTMLButtonElement>(`[role="checkbox"][aria-label="${label}"]`)!;

describe('permission rules', () => {
  it('keep write behind read and clear fields and rows without access', () => {
    expect(toggleOp(all, all, 'read')).toEqual({ ops: [], read: null, write: null, scope: null });
    expect(toggleOp({ ops: [], read: null, write: null, scope: null }, all, 'update')).toEqual({ ops: ['read', 'update'], read: null, write: null, scope: null });
    expect(normalize({ ops: ['read', 'revise'], read: ['name'], write: ['name'], scope: 'owner' }, all)).toEqual({ ops: ['read'], read: ['name'], write: null, scope: 'owner' });
    expect(normalize({ ops: ['read'], read: null, write: null, scope: null }, { ...all, scope: 'owner' }).scope).toBe('owner');
  });
});

describe('Controls change bar', () => {
  it('collects drafts, reviews and discards them, then saves them in one request', async () => {
    const { host, sent } = open('/controls/permissions?role=editor', () => ({ status: 200, body: { version: 2, saved: 1 } }));
    await until(() => expect(checkbox(host, 'Create Customer')).toBeTruthy());
    expect(checkbox(host, 'Create Customer').getAttribute('aria-checked')).toBe('true');
    expect(bar()).toBeNull();

    click(checkbox(host, 'Create Customer'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    expect(text(host.querySelector('.ctl-tag'))).toBe('Unsaved');
    click(byText(bar()!, 'button', 'Review'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('editor · Customer'));
    expect(text(document.querySelector('.modal .ctl-from'))).toBe('Read, Create, Update, Delete · all records (file)');
    expect(text(document.querySelector('.modal .ctl-to'))).toBe('Read, Update, Delete · all records');
    click(byText(document.body, '.modal button', 'Discard all'));
    await until(() => expect(bar()).toBeNull());
    expect(checkbox(host, 'Create Customer').getAttribute('aria-checked')).toBe('true');

    click(checkbox(host, 'Read Customer'));
    await until(() => expect(checkbox(host, 'Update Customer').getAttribute('aria-checked')).toBe('false'));
    click(checkbox(host, 'Read Customer'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(bar()).toBeNull());
    const posted = sent.filter(item => item.method === 'POST');
    expect(posted).toHaveLength(1);
    expect(posted[0].headers['x-csrf-token']).toBe('token');
    expect(posted[0].body).toEqual({ changes: [{ kind: 'perm', key: 'editor|Customer', value: { ops: ['read'], read: null, write: null, scope: null } }] });
    expect(lastToast()).toBe('1 change saved');
    await until(() => expect(text(host.querySelector('.ctl-ovrow'))).toContain('Override'));
  });

  it('keeps every draft and shows the server message per item when a save is refused', async () => {
    const { host, sent } = open('/controls/security', () => ({ status: 422,
      body: { error: 'Validation failed', fields: { 'changes.0': 'Session length can only go down, from 8 hours to 1.' } } }));
    await until(() => expect(host.querySelector('select[aria-label="Session length"]')).toBeTruthy());
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="Session length"]')!;
    expect([...select.options].map(option => option.value)).toEqual(['1', '4', '8']);
    select.value = '4';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    expect(text(host.querySelector('.ctl-setrow'))).not.toContain('Changed');
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Session length can only go down, from 8 hours to 1.'));
    expect(text(bar())).toContain('1 unsaved change');
    expect(sent.filter(item => item.method === 'POST')[0].body.changes).toEqual([{ kind: 'setting', key: 'security.session_hours', value: 4 }]);
    expect(byText(host, '.ctl-setrow', /^Session length Changed/)).toBeTruthy();
  });

  it('shows 404 for people without access as a page that is not available', async () => {
    window.history.replaceState(null, '', '/controls');
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 2, username: 'rahul', role: 'editor' }, csrf: 't' }, etag: null })],
    ] as any);
    const router = controlsRouter();
    const stop = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
    running.push(() => { dispose(); stop(); });
    await until(() => expect(text(host)).toContain('This page is not available'));
  });
});

describe('Controls safety', () => {
  afterEach(() => { me = { id: 1, username: 'owner', role: 'admin' }; });

  it('checks addresses against allowed networks', () => {
    expect(ipAllowed('10.2.3.4', [])).toBe(true);
    expect(ipAllowed('10.2.3.4', ['10.0.0.0/8'])).toBe(true);
    expect(ipAllowed('127.0.0.1', ['10.0.0.0/8', '192.168.1.5/32'])).toBe(false);
    expect(ipAllowed('2001:db8::1', ['2001:db8::/32'])).toBe(true);
    expect(ipAllowed('2001:db9::1', ['2001:db8::/32'])).toBe(false);
    expect(ipAllowed('', ['10.0.0.0/8'])).toBe(false);
  });

  it('asks before saving allowed networks that leave out your own address', async () => {
    const { host, sent } = open('/controls/security', () => ({ status: 200, body: { version: 2, saved: 1 } }));
    await until(() => expect(host.querySelector('textarea[aria-label="Allowed networks"]')).toBeTruthy());
    const area = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Allowed networks"]')!;
    area.value = '10.0.0.0/8';
    area.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Your address, 127.0.0.1, is not in the allowed networks.'));
    expect(sent.filter(item => item.method === 'POST')).toHaveLength(0);
    click(byText(document.body, '.modal button', 'Save anyway'));
    await until(() => expect(sent.filter(item => item.method === 'POST')).toHaveLength(1));
  });

  it('shows admin accounts and role controls read only to roles that are not admin', async () => {
    me = { id: 3, username: 'ops', role: 'ops' };
    const { host } = open('/controls/roles', () => ({ status: 200, body: {} }));
    await until(() => expect(host.querySelector('[role="switch"][aria-label="Opens Controls for editor"]')).toBeTruthy());
    expect(host.querySelector<HTMLButtonElement>('[role="switch"][aria-label="Opens Controls for editor"]')!.disabled).toBe(true);
    expect(byText(host, 'button', 'New role')).toBeNull();
    click(byText(host, '.ctl-nav-item', /^Users/));
    await until(() => expect(byText(host, 'tr', /owner/)).toBeTruthy());
    click(byText(host, 'tr', /owner/));
    await until(() => expect(text(document.querySelector('.drawer'))).toContain('Only an admin can change an admin account.'));
    expect(document.querySelector<HTMLSelectElement>('.drawer select')!.disabled).toBe(true);
  });
});
