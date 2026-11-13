// Sign-in rules: the administrator username filled in by config.py, the first password of the built-in
// Administrator after a development link, two-step set-up after it, and its protected row in Controls > Users.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { byText, click, recorded, requests, screen, start, stopAll, text, typeInto, until } from './screens-helpers';
import { server } from './server';

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

const submit = (host: HTMLElement) => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
const reply = (body: unknown, status = 200) => ({ status, body, etag: null });
const anonymous = (extra: Record<string, unknown>) => reply({ ...(recorded.session_anonymous.body as object), ...extra });
const administrator = { id: 1, username: 'admin', role: 'admin' };

describe('the sign-in page', () => {
  it('starts with the administrator username when config.py asks for it, and never offers a shortcut', async () => {
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => anonymous({ sign_in: { username: 'admin' } })],
      ['POST', /\/api\/auth\/login$/, recorded.login],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/login');
    await until(() => expect(host.querySelector<HTMLInputElement>('#ev-username')?.value).toBe('admin'));
    expect(host.querySelector('#ev-password')!.hasAttribute('autofocus')).toBe(true);
    expect(byText(host, 'button', /administrator/i)).toBeFalsy();
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'a long enough password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(requests(sent, 'POST', /\/api\/auth\/login$/)[0].body).toEqual({ username: 'admin', password: 'a long enough password' });
  });

  it('fills in a username from ?user= and leaves it empty otherwise', async () => {
    const first = start([['GET', /\/api\/auth\/session$/, recorded.session_anonymous]], '/login?user=sara');
    await until(() => expect(first.host.querySelector<HTMLInputElement>('#ev-username')?.value).toBe('sara'));
    stopAll();
    const second = start([['GET', /\/api\/auth\/session$/, recorded.session_anonymous]], '/login');
    await until(() => expect(second.host.querySelector('#ev-username')).toBeTruthy());
    expect(second.host.querySelector<HTMLInputElement>('#ev-username')!.value).toBe('');
  });

  it('asks first-run setup for the minimum config.py sets', async () => {
    const { host } = start([['GET', /\/api\/auth\/session$/, () => anonymous({ setup: true, password_min: 1 })]], '/login');
    await until(() => expect(host.querySelector('#ev-password')).toBeTruthy());
    expect(host.querySelector('#ev-password')!.getAttribute('minlength')).toBe('1');
    expect(text(host.querySelector('#ev-password-hint'))).toBe('Any password will do.');
  });
});

describe('the first password', () => {
  it('sets the password, then two-step sign-in, then opens the app', async () => {
    let state: 'link' | 'ticket' | 'in' = 'link';
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => state === 'in' ? recorded.session
        : reply({ user: administrator, csrf: 'token', password_needed: true, password_min: 15, application: { name: 'Acme' } })],
      ['POST', /\/api\/auth\/password$/, () => { state = 'ticket'; return reply({ two_step: 'setup', ticket: 'tkt', secret: 'JBSWY3DPEHPK3PXP', uri: 'otpauth://totp/x' }); }],
      ['POST', /\/api\/auth\/login$/, () => { state = 'in'; return reply({ user: administrator, csrf: 'fresh', backup_codes: ['aaaa-bbbb'] }); }],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set your password'));
    expect(text(host.querySelector('.ev-login-card > p'))).toBe('You are signed in as admin. Choose a password to use from now on.');
    expect(text(host.querySelector('#ev-new-password-hint'))).toBe('Use at least 15 characters.');
    // Checked in the browser first: the length, then the repeat.
    typeInto(host.querySelector<HTMLInputElement>('#ev-new-password')!, 'short');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('Use at least 15 characters.'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-new-password')!, 'a long enough password');
    typeInto(host.querySelector<HTMLInputElement>('#ev-again-password')!, 'a different password!');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe("The two passwords don't match."));
    typeInto(host.querySelector<HTMLInputElement>('#ev-again-password')!, 'a long enough password');
    submit(host);
    // Admins need two-step sign-in: the set-up follows straight away.
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set up two-step sign-in'));
    expect(requests(sent, 'POST', /\/api\/auth\/password$/)[0].body).toEqual({ password: 'a long enough password' });
    typeInto(host.querySelector<HTMLInputElement>('input[autocomplete="one-time-code"], #tfa-code, .ev-login-card input')!, '123456');
    submit(host);
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(requests(sent, 'POST', /\/api\/auth\/login$/)[0].body).toEqual({ ticket: 'tkt', code: '123456' });
  });

  it('opens the app at once when two-step sign-in is not required', async () => {
    let saved = false;
    const { host } = start([
      ['GET', /\/api\/auth\/session$/, () => saved ? recorded.session
        : reply({ user: administrator, csrf: 'token', password_needed: true, password_min: 1 })],
      ['POST', /\/api\/auth\/password$/, () => { saved = true; return reply({ password: true }); }],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set your password'));
    expect(text(host.querySelector('#ev-new-password-hint'))).toBe('Any password will do.');
    typeInto(host.querySelector<HTMLInputElement>('#ev-new-password')!, '1');
    typeInto(host.querySelector<HTMLInputElement>('#ev-again-password')!, '1');
    submit(host);
    await until(() => expect(location.pathname).toBe('/app/customer'));
  });
});

describe('Controls > Users', () => {
  const roles = [
    { id: 'admin', kind: 'framework', base: null, realm: 'desk', opens_controls: true, description: 'Full access to every app. Opens Controls.', users: 2, app: null, declared: true },
    { id: 'editor', kind: 'framework', base: null, realm: 'desk', opens_controls: false, description: 'Reads and changes records.', users: 1, app: null, declared: true },
  ];
  const state = {
    instance: { name: 'Acme', host: 'localhost', evlet: '0.4.2', version: 1 }, me: { id: 2, username: 'owner', role: 'admin' }, apps: [], roles, models: [], scopes: { '': 'All records' },
    settings: { 'security.password_min': { file: 15, value: 4 }, 'security.session_hours': { file: 12, value: 12 }, 'security.lockout': { file: 5, value: 5 },
      'security.two_step': { file: false, value: false }, 'security.two_step_roles': { file: [], value: [] }, 'security.controls_ips': { file: [], value: [] } },
    controls: { allow_ips: [], require_two_step: false }, admin_rules: { password_min: 15, two_step: true, sign_in: false },
    attention: [], overrides: 0, users: { active: 3, total: 3 }, audit: [], two_step: true, client: '127.0.0.1',
  };

  function open(path: string) {
    window.history.replaceState(null, '', path);
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => reply({ user: state.me, csrf: 'token' })],
      ['GET', /\/api\/_controls\/state$/, () => reply(state)],
      ['GET', /\/api\/_controls\/overrides$/, () => reply({ version: 1, problems: [], items: [] })],
      ['GET', /\/api\/_controls\/users/, () => reply({ offset: 0, limit: 100, total: 3, items: [
        { id: 1, username: 'admin', role: 'admin', active: true, sessions: 0, builtin: true, password_set: false },
        { id: 2, username: 'owner', role: 'admin', active: true, sessions: 1, builtin: false, password_set: true },
        { id: 3, username: 'rahul', role: 'editor', active: true, sessions: 1, builtin: false, password_set: true }] })],
      ['PATCH', /\/api\/_controls\/users\/\d+$/, () => reply(null, 204)],
    ] as any);
    const router = controlsRouter();
    const stop = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
    running.push(() => { dispose(); stop(); host.remove(); });
    return { host, sent: stub.sent };
  }

  it('marks the Administrator as built in and locks its role and status', async () => {
    const { host } = open('/controls/users');
    await until(() => expect(byText(host, 'tr', /Built in/)).toBeTruthy());
    expect(text(byText(host, 'tr', /Built in/)!.querySelector('.ctl-user'))).toContain('Built in');
    expect(text(byText(host, 'tr', /rahul/)!.querySelector('.ctl-user'))).not.toContain('Built in');
    // Admins need two-step sign-in from config.py; the editor does not.
    expect(text(byText(host, 'tr', /Built in/))).toContain('Off · required');
    expect(text(byText(host, 'tr', /rahul/))).not.toContain('required');
    click(byText(host, 'tr', /Built in/)!);
    await until(() => expect(document.querySelector('.drawer')).toBeTruthy());
    const drawer = document.querySelector('.drawer')!;
    expect(text(drawer)).toContain("The System Administrator is built in. Its username, name and role never change, and it can't be deactivated. It has no password yet.");
    expect(drawer.querySelector<HTMLButtonElement>('#ctl-u-role')!.disabled).toBe(true);
    expect(drawer.querySelector<HTMLButtonElement>('[role="switch"]')!.disabled).toBe(true);
    // Its password follows the admin rule from config.py, not the 4 characters Controls set for everyone else.
    expect(drawer.querySelector('#ctl-u-pass')!.getAttribute('placeholder')).toBe('At least 15 characters');
  });

  it('keeps every control for other people', async () => {
    const { host } = open('/controls/users');
    await until(() => expect(byText(host, 'tr', /rahul/)).toBeTruthy());
    click(byText(host, 'tr', /rahul/)!);
    await until(() => expect(document.querySelector('.drawer')).toBeTruthy());
    const drawer = document.querySelector('.drawer')!;
    expect(text(drawer)).not.toContain('built in');
    expect(drawer.querySelector<HTMLButtonElement>('#ctl-u-role')!.disabled).toBe(false);
    expect(drawer.querySelector('#ctl-u-pass')!.getAttribute('placeholder')).toBe('At least 4 characters');
  });

  it('shows the admin rules from config.py on Security and lets Controls lower the minimum for everyone else', async () => {
    const { host } = open('/controls/security');
    await until(() => expect(host.querySelector('.ctl-admin-rules')).toBeTruthy());
    expect(text(host.querySelector('.ctl-admin-rules'))).toContain('Admins need at least 15 characters.');
    expect(text(host.querySelector('.ctl-admin-rules'))).toContain('Required');
    const input = host.querySelector<HTMLInputElement>('input[aria-label="Minimum password length"]')!;
    expect(input.getAttribute('min')).toBe('1');
    expect(host.querySelector('[role="switch"][aria-label="Two-step sign-in for everyone"]')).toBeTruthy();
    expect(byText(host, '.ctl-chip', /admin/)!.hasAttribute('disabled')).toBe(true);
  });

  it('requires two-step for roles that open Controls, and only an admin can switch that off', async () => {
    const ops = { id: 'ops', kind: 'custom', base: 'editor', realm: 'desk', opens_controls: true, description: '', users: 1, app: null, declared: false } as unknown as (typeof roles)[number];
    state.roles = [...roles, ops];
    state.settings = { ...state.settings, 'security.controls_two_step': { file: true, value: true } } as typeof state.settings;
    try {
      const { host } = open('/controls/security');
      await until(() => expect(host.querySelector('[aria-label="Two-step sign-in for roles that open Controls"]')).toBeTruthy());
      const toggle = host.querySelector<HTMLButtonElement>('[aria-label="Two-step sign-in for roles that open Controls"]')!;
      expect(toggle.getAttribute('aria-checked')).toBe('true');
      expect(toggle.disabled).toBe(false);
      // The ops chip is on and locked, the editor chip is free.
      expect(byText(host, '.ctl-chip', /ops/)!.getAttribute('aria-pressed')).toBe('true');
      expect(byText(host, '.ctl-chip', /ops/)!.hasAttribute('disabled')).toBe(true);
      expect(byText(host, '.ctl-chip', /editor/)!.hasAttribute('disabled')).toBe(false);
      state.me = { ...state.me, role: 'ops' };
      const other = open('/controls/security').host;
      await until(() => expect(other.querySelector('[aria-label="Two-step sign-in for roles that open Controls"]')).toBeTruthy());
      expect(other.querySelector<HTMLButtonElement>('[aria-label="Two-step sign-in for roles that open Controls"]')!.disabled).toBe(true);
      expect(text(other)).toContain('Only an admin can change this.');
    } finally {
      state.roles = roles;
      state.me = { ...state.me, role: 'admin' };
    }
  });
});
