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
  { id: 'portal', kind: 'framework', base: null, realm: 'portal', opens_controls: false, description: 'Signs in on portal hosts. Sees only what an app grants.', users: 0, app: null, declared: true },
];
let me = { id: 1, username: 'owner', role: 'admin' };
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '0.1.3', version: 1 },
  me: { ...me },
  apps: [], roles,
  models: [{ name: 'Customer', label: 'Customer', app: null, scopes: [null, 'owner'],
    fields: [{ name: 'name', label: 'Name', read_only: false }, { name: 'email', label: 'Email', read_only: false }, { name: 'owner', label: 'Owner', read_only: false }] }],
  scopes: { '': 'All records', owner: 'Records they own' },
  settings: { 'security.password_min': { file: 15, value: 15 }, 'security.session_hours': { file: 12, value: 12 }, 'security.lockout': { file: 5, value: 5 },
    'security.two_step_roles': { file: ['admin'], value: ['admin'] }, 'security.controls_ips': { file: [], value: [] } },
  controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0, users: { active: 3, total: 3 }, audit: [], two_step: false, client: '127.0.0.1',
});
const item = (kind: 'perm' | 'setting', key: string, file: unknown, value: unknown = null): OverrideItem =>
  ({ kind, key, file, value, changed_by: value === null ? null : 'owner', changed_at: value === null ? null : 1790000000, source: 'framework' });
const overrides = (saved: Record<string, unknown> = {}) => ({ version: 1, problems: [], items: [
  ...['admin', 'editor'].map(role => item('perm', `${role}|Customer`, all, saved[`${role}|Customer`] ?? null)),
  item('perm', 'viewer|Customer', { ...all, ops: ['read'] }, saved['viewer|Customer'] ?? null),
  item('setting', 'security.password_min', 15), item('setting', 'security.session_hours', 12, saved['security.session_hours'] ?? null),
  item('setting', 'security.lockout', 5), item('setting', 'security.two_step_roles', ['admin']), item('setting', 'security.controls_ips', []),
] });

function open(path: string, save: (sent: Sent) => { status: number; body: any }) {
  window.history.replaceState(null, '', path);
  let saved: Record<string, unknown> = {};
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
    ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(), etag: null })],
    ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: overrides(saved), etag: null })],
    ['GET', /\/api\/_controls\/users/, () => ({ status: 200, etag: null, body: { offset: 0, limit: 100, total: 2, items: [
      { id: 1, username: 'owner', role: 'admin', active: true, sessions: 1 }, { id: 2, username: 'rahul', role: 'editor', active: true, sessions: 1, keys: 2 }] } })],
    ['PATCH', /\/api\/_controls\/users\/\d+$/, () => ({ status: 204, body: null, etag: null })],
    ['POST', /\/api\/_settings\/me\/two-step\/verify$/, (sent: Sent) => sent.body.code === '123456'
      ? { status: 200, body: { verified: 1, until: 601 }, etag: null }
      : { status: 422, body: { error: 'Validation failed', fields: { code: 'Enter a current code from your app, or a backup code.' } }, etag: null }],
    ['POST', /\/api\/_controls\/roles$/, () => ({ status: 201, body: { items: [] }, etag: null })],
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
    // Unpost and repost grants are kept; repost needs update, like the server says.
    expect(normalize({ ops: ['read', 'update', 'unpost', 'repost'], read: null, write: null, scope: null }, all).ops).toEqual(['read', 'update', 'unpost', 'repost']);
    expect(normalize({ ops: ['read', 'unpost', 'repost'], read: null, write: null, scope: null }, all).ops).toEqual(['read', 'unpost']);
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
      body: { error: 'Validation failed', fields: { 'changes.0': 'Session length can only go down, from 12 hours to 1.' } } }));
    await until(() => expect(host.querySelector('select[aria-label="Session length"]')).toBeTruthy());
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="Session length"]')!;
    expect([...select.options].map(option => option.value)).toEqual(['1', '4', '8', '12']);
    select.value = '4';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    expect(text(host.querySelector('.ctl-setrow'))).not.toContain('Changed');
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Session length can only go down, from 12 hours to 1.'));
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
  it('asks for a two-step code when a change needs one, then sends the change again', async () => {
    let asked = 0;
    const verify = { status: 403, body: { error: 'VERIFY_TWO_STEP: Enter a code from your authenticator app to make this change.' } };
    const { host, sent } = open('/controls/permissions?role=editor', () => asked++ ? { status: 200, body: { version: 2, saved: 1 } } : verify);
    await until(() => expect(checkbox(host, 'Create Customer')).toBeTruthy());
    click(checkbox(host, 'Create Customer'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Enter your two-step code'));
    const input = document.querySelector<HTMLInputElement>('#ctl-verify-code')!;
    input.value = '000000';
    input.dispatchEvent(new Event('input'));
    click(byText(document.body, '.modal button', 'Confirm'));
    await until(() => expect(text(document.querySelector('.modal .ctl-err'))).toBe('Enter a current code from your app, or a backup code.'));
    input.value = '123456';
    input.dispatchEvent(new Event('input'));
    click(byText(document.body, '.modal button', 'Confirm'));
    await until(() => expect(bar()).toBeNull());
    expect(document.querySelector('.modal')).toBeNull();
    const posts = sent.filter(item => item.method === 'POST').map(item => item.url.replace(/^.*\/api/, '/api'));
    expect(posts).toEqual(['/api/_controls/overrides', '/api/_settings/me/two-step/verify', '/api/_settings/me/two-step/verify', '/api/_controls/overrides']);
    expect(lastToast()).toBe('1 change saved');
  });

  it('keeps the change when the code dialog is cancelled', async () => {
    const { host, sent } = open('/controls/permissions?role=editor', () => ({ status: 403, body: { error: 'VERIFY_TWO_STEP: Enter a code from your authenticator app to make this change.' } }));
    await until(() => expect(checkbox(host, 'Create Customer')).toBeTruthy());
    click(checkbox(host, 'Create Customer'));
    await until(() => expect(text(bar())).toContain('1 unsaved change'));
    click(byText(bar()!, 'button', 'Save changes'));
    await until(() => expect(document.querySelector('#ctl-verify-code')).toBeTruthy());
    click(byText(document.body, '.modal button', 'Cancel'));
    await until(() => expect(lastToast()).toBe('The change was not made. It needs a code from your authenticator app.'));
    expect(document.querySelector('.modal')).toBeNull();
    expect(text(bar())).toContain('1 unsaved change');
    expect(sent.filter(item => item.url.endsWith('/verify'))).toHaveLength(0);
  });

  it('signs a user out everywhere and revokes their API keys only when ticked', async () => {
    const { host, sent } = open('/controls/users', () => ({ status: 200, body: {} }));
    await until(() => expect(byText(host, 'tr', /rahul/)).toBeTruthy());
    click(byText(host, 'tr', /rahul/));
    await until(() => expect(byText(document.body, '.drawer button', 'Sign out everywhere')).toBeTruthy());
    click(byText(document.body, '.drawer button', 'Sign out everywhere'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Sign rahul out everywhere?'));
    const box = byText(document.body, '.modal [role="checkbox"]', 'Also revoke API keys (2 active)')!;
    expect(box.getAttribute('aria-checked')).toBe('false');
    click(byText(document.body, '.modal button', 'Sign out everywhere'));
    await until(() => expect(sent.filter(item => item.method === 'PATCH')).toHaveLength(1));
    expect(sent.find(item => item.method === 'PATCH')!.body).toEqual({ sessions: 'end', revoke_keys: false });
    click(byText(document.body, '.drawer button', 'Sign out everywhere'));
    click(byText(document.body, '.modal [role="checkbox"]', 'Also revoke API keys (2 active)')!);
    click(byText(document.body, '.modal button', 'Sign out everywhere'));
    await until(() => expect(sent.filter(item => item.method === 'PATCH')).toHaveLength(2));
    expect(sent.filter(item => item.method === 'PATCH')[1].body).toEqual({ sessions: 'end', revoke_keys: true });
  });

  it('creates a portal role that starts from portal and never opens Controls', async () => {
    const { host, sent } = open('/controls/roles', () => ({ status: 200, body: {} }));
    await until(() => expect(host.querySelector('[role="switch"][aria-label="Opens Controls for portal"]')).toBeTruthy());
    expect(host.querySelector<HTMLButtonElement>('[role="switch"][aria-label="Opens Controls for portal"]')!.disabled).toBe(true);
    click(byText(host, 'button', 'New role'));
    await until(() => expect(document.querySelector('#ctl-r-realm')).toBeTruthy());
    const realm = document.querySelector<HTMLSelectElement>('#ctl-r-realm')!;
    realm.value = 'portal';
    realm.dispatchEvent(new Event('change'));
    await until(() => expect(document.querySelector<HTMLSelectElement>('#ctl-r-base')!.disabled).toBe(true));
    const id = document.querySelector<HTMLInputElement>('#ctl-r-id')!;
    id.value = 'partner';
    id.dispatchEvent(new Event('input'));
    click(byText(document, 'button', 'Create role'));
    await until(() => expect(sent.some(item => item.method === 'POST' && item.url.endsWith('/api/_controls/roles'))).toBe(true));
    expect(sent.find(item => item.url.endsWith('/api/_controls/roles'))!.body).toEqual({ id: 'partner', base: 'portal', realm: 'portal', description: '' });
  });
});
