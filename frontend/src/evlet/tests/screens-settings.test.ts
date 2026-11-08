import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { choiceValues, choose } from './ui-helpers';
import { copy } from './server';
import { setRegion } from '../model/format';

afterEach(() => { stopAll(); setRegion({}); document.documentElement.removeAttribute('dir'); });

const me = (change: (body: any) => void = () => {}) => { const value = copy(screen.settings_me); change(value.body); return value; };
const routes = (extra: Route[] = [], settings = me()): Route[] => [...adminRoutes(), ['GET', /\/api\/_settings\/me$/, settings],
  ['PATCH', /\/api\/_settings\/me\/prefs$/, screen.settings_prefs], ...extra];

describe('the account area', () => {
  it('opens a menu in the design order, with Controls only for roles that open it', async () => {
    const { host } = start(routes(), '/docs');
    await until(() => expect(text(host.querySelector('.me .n'))).toBe('Owner'));
    expect(text(host.querySelector('.me .e'))).toBe('Managing Director');
    click(host.querySelector('.me'));
    expect(text(document.querySelector('.me-head strong'))).toBe('Owner Admin');
    // The name block is styled apart from the avatar, so the avatar keeps its centred letter.
    expect(document.querySelector('.me-head .me-who strong')).not.toBeNull();
    expect(document.querySelector('.me-head .me-who .avatar')).toBeNull();
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).toEqual(['Your profile', 'Appearance', 'Sign-in & security', 'Change password', 'Dark theme', 'All settings', 'Controls↗', 'Sign out']);
    click(byText(document.body, '.pop [role="menuitem"]', 'All settings'));
    await until(() => expect(location.pathname).toBe('/settings/profile'));
    stopAll();
    const viewer = start(routes([], me(body => { body.controls = false; })), '/docs');
    await until(() => expect(viewer.host.querySelector('.me')).not.toBeNull());
    click(viewer.host.querySelector('.me'));
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).not.toContain('Controls↗');
  });
});

describe('change password from the account menu', () => {
  const PASSWORD = /\/api\/_settings\/me\/password$/;
  const openDialog = async (extra: Route[] = [], settings = me()) => {
    const started = start(routes(extra, settings), '/docs');
    await until(() => expect(started.host.querySelector('.me')).not.toBeNull());
    await until(() => expect(text(started.host.querySelector('.me .n'))).toBe('Owner'));
    click(started.host.querySelector('.me'));
    click(byText(document.body, '.pop [role="menuitem"]', 'Change password'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Change password'));
    return started;
  };
  const fill = (current: string, next: string, again: string) => {
    typeInto(document.querySelector<HTMLInputElement>('.modal #pw-cur')!, current);
    typeInto(document.querySelector<HTMLInputElement>('.modal #pw-new')!, next);
    typeInto(document.querySelector<HTMLInputElement>('.modal #pw-again')!, again);
  };
  const send = () => click(byText(document.body, '.modal-f button', 'Change password'));

  it('opens a dialog with labelled fields, the workspace rule and focus on the first field; Esc closes it', async () => {
    await openDialog();
    const inputs = [...document.querySelectorAll<HTMLInputElement>('.modal input')];
    expect(inputs.map(input => [input.id, input.type, input.autocomplete])).toEqual([
      ['pw-cur', 'password', 'current-password'], ['pw-new', 'password', 'new-password'], ['pw-again', 'password', 'new-password']]);
    for (const input of inputs) expect(document.querySelector(`.modal label[for="${input.id}"]`)).not.toBeNull();
    expect(document.activeElement?.id).toBe('pw-cur');
    expect(text(document.querySelector('.modal #pw-rules'))).toBe('At least 15 characters.');
    press(document.activeElement!, 'Escape');
    expect(document.querySelector('.modal')).toBeNull();
  });

  it('is hidden where Settings has no password change', async () => {
    const { host } = start(routes([], me(body => { body.pages = ['profile', 'appearance', 'notifications']; })), '/docs');
    await until(() => expect(text(host.querySelector('.me .n'))).toBe('Owner'));
    click(host.querySelector('.me'));
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).not.toContain('Change password');
  });

  it('checks a short password and a mismatch before sending anything', async () => {
    const { sent } = await openDialog();
    fill('fake current value', 'short', 'short');
    send();
    expect(text(document.querySelector('.modal #pw-new-err'))).toBe('Your workspace needs at least 15 characters.');
    expect(document.querySelector('.modal #pw-new-err')!.getAttribute('role')).toBe('alert');
    expect(document.querySelector('.modal #pw-new')!.getAttribute('aria-invalid')).toBe('true');
    fill('fake current value', 'a fake new long value', 'a different fake value');
    send();
    expect(text(document.querySelector('.modal #pw-again-err'))).toBe("The two new passwords don't match.");
    expect(requests(sent, 'POST', /password$/)).toHaveLength(0);
  });

  it('shows the server error under the field it names, and a general one as from the server', async () => {
    let reply: any = { status: 422, etag: null, body: { error: 'Validation failed', fields: { current: 'That password is not right.' } } };
    const { sent } = await openDialog([['POST', PASSWORD, () => reply]]);
    fill('a wrong fake value', 'a fake new long value', 'a fake new long value');
    send();
    await until(() => expect(text(document.querySelector('.modal #pw-cur-err'))).toBe('That password is not right.'));
    expect(document.querySelector('.modal')).not.toBeNull();
    reply = { status: 409, etag: null, body: { error: 'Your password changed meanwhile. Reload the page.' } };
    send();
    await until(() => expect(text(document.querySelector('.modal [role="alert"]'))).toBe('The server said: Your password changed meanwhile. Reload the page.'));
    expect(requests(sent, 'POST', /password$/)).toHaveLength(2);
  });

  it('sends the change, closes and says other devices were signed out', async () => {
    const { sent } = await openDialog([['POST', PASSWORD, { status: 200, etag: null, body: { changed: true, revoked_keys: 0 } }]]);
    fill('fake current value', 'a fake new long value', 'a fake new long value');
    send();
    await until(() => expect(document.querySelector('.modal')).toBeNull());
    expect(requests(sent, 'POST', /password$/)[0].body).toEqual({ current: 'fake current value', new: 'a fake new long value' });
    expect(lastToast()).toBe('Password changed. Other devices were signed out.');
  });
});

describe('personal pages', () => {
  it('saves the profile as you go and asks for a confirmation link to change the email', async () => {
    const { host, sent } = start(routes([
      ['PATCH', /\/api\/_settings\/me\/profile$/, sent => ({ status: 200, etag: null, body: { ...screen.settings_me.body.profile, ...sent.body } })],
      ['POST', /\/api\/_settings\/me\/email$/, { status: 200, etag: null, body: { sent: 'new@example.com', link: 'http://127.0.0.1:8000/settings/profile?confirm=abc' } }],
    ]), '/settings/profile');
    await until(() => expect(text(host.querySelector('.set-head h1'))).toBe('Profile'));
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Profile');
    typeInto(host.querySelector<HTMLInputElement>('#pf-title')!, 'Director');
    await until(() => expect(requests(sent, 'PATCH', /profile$/)[0]?.body).toEqual({ title: 'Director' }));
    await until(() => expect(text(host.querySelector('.me .e'))).toBe('Director'));
    click(byText(host, 'button', 'Change'));
    typeInto(document.querySelector<HTMLInputElement>('#ce')!, 'not an email');
    click(byText(document.body, '.modal button', 'Send link'));
    expect(text(document.querySelector('.modal .err'))).toBe('Enter a valid email address.');
    typeInto(document.querySelector<HTMLInputElement>('#ce')!, 'new@example.com');
    click(byText(document.body, '.modal button', 'Send link'));
    expect(text(document.querySelector('.modal .err'))).toBe('Enter your password.');
    typeInto(document.querySelector<HTMLInputElement>('#ra-password')!, 'my password');
    click(byText(document.body, '.modal button', 'Send link'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Confirmation link'));
    expect(requests(sent, 'POST', /email$/)[0].body).toEqual({ email: 'new@example.com', password: 'my password' });
    // The last page is remembered on the server.
    expect(requests(sent, 'PATCH', /prefs$/).map(item => item.body)).toEqual([]);
  });

  it('applies the region everywhere: the date format changes the dates in a list', async () => {
    const { host, sent } = start(routes([['GET', /\/api\/order\?/, screen.orders_open]]), '/settings/appearance');
    await until(() => expect(text(host.querySelector('.set-head h1'))).toBe('Appearance & region'));
    const date = host.querySelector<HTMLElement>('button[aria-label="Date format"]')!;
    expect(choiceValues(date)).toEqual(['d MMM yyyy', 'dd-MM-yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy']);
    choose(date, 'yyyy-MM-dd');
    await until(() => expect(lastToast()).toBe('Saved'));
    expect(requests(sent, 'PATCH', /prefs$/).map(item => item.body)).toEqual([{ last_page: 'appearance' }, { date: 'yyyy-MM-dd' }]);
    click(byText(host, '.nav-item', 'Orders'));
    await until(() => expect(text(host.querySelector('tbody tr[data-key]'))).toContain('2026-10-20'));
  });

  it('shows sidebar counts once, without fetching them again on every saved preference', async () => {
    const { host, sent } = start(routes([['GET', /\/api\/(customer|product|order|contact)\?/, screen.contacts]], me(body => { body.prefs.counts = true; })), '/settings/appearance');
    await until(() => expect(host.querySelector('.nav-item .count')).not.toBeNull());
    const counted = () => sent.filter(item => /\/api\/(customer|product|order|contact)\?/.test(item.url)).length;
    const before = counted();
    click(byText(host, '.set-choice', /^Compact/));
    await until(() => expect(lastToast()).toBe('Saved'));
    expect(counted()).toBe(before);
  });

  it('turns on Enter moves to the next field, off by default', async () => {
    const { host, sent } = start(routes(), '/settings/appearance');
    const toggle = () => host.querySelector<HTMLButtonElement>('[role="switch"][aria-label="Enter moves to the next field"]');
    await until(() => expect(toggle()).not.toBeNull());
    expect(toggle()!.getAttribute('aria-checked')).toBe('false');
    click(toggle());
    await until(() => expect(requests(sent, 'PATCH', /\/api\/_settings\/me\/prefs$/).map(item => item.body)).toContainEqual({ enter_next: true }));
  });

  it('starts with the region from the server and switches Arabic to right-to-left', async () => {
    const { host } = start(routes([['GET', /\/api\/order\?/, screen.orders_open]], me(body => { body.prefs.date = 'd MMM yyyy'; body.prefs.lang = 'ar'; })), '/app/order');
    await until(() => expect(text(host.querySelector('tbody tr[data-key]'))).toContain('20 Oct 2026'));
    expect(document.documentElement.dir).toBe('rtl');
  });

  it('turns on two-step sign-in and shows the backup codes once', async () => {
    const { host, sent } = start(routes([
      ['POST', /\/api\/_settings\/me\/two-step$/, { status: 200, etag: null, body: { secret: 'JBSWY3DPEHPK3PXP', uri: 'otpauth://totp/x' } }],
      ['POST', /\/api\/_settings\/me\/two-step\/confirm$/, { status: 200, etag: null, body: { codes: ['abcd-efgh', 'jkmn-pqrs'] } }],
    ], me(body => { body.security.two_step.available = true; })), '/settings/security');
    await until(() => expect(byText(host, 'button', 'Set up')).not.toBeNull());
    click(byText(host, 'button', 'Set up'));
    await until(() => expect(document.querySelector('#ra-password')).not.toBeNull());
    expect(document.querySelector('#ra-code')).toBeNull();
    typeInto(document.querySelector<HTMLInputElement>('#ra-password')!, 'my password');
    click(byText(document.body, '.modal button', 'Continue'));
    await until(() => expect(text(document.querySelector('.set-key code'))).toBe('JBSW Y3DP EHPK 3PXP'));
    expect(requests(sent, 'POST', /two-step$/)[0].body).toEqual({ password: 'my password' });
    typeInto(document.querySelector<HTMLInputElement>('#tfa-code')!, '123456');
    click(byText(document.body, '.modal button', 'Turn on'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Backup codes'));
    expect([...document.querySelectorAll('.set-codes span')].map(text)).toEqual(['abcd-efgh', 'jkmn-pqrs']);
    expect(requests(sent, 'POST', /confirm$/)[0].body).toEqual({ code: '123456' });
  });

  it('asks only for a code before making new backup codes when two-step sign-in is on', async () => {
    const { host, sent } = start(routes([['POST', /\/api\/_settings\/me\/two-step\/codes$/, { status: 200, etag: null, body: { codes: ['abcd-efgh'] } }]],
      me(body => { body.security.two_step = { on: true, since: 1793534400, agent: 'a browser', codes: 3, required: false, available: true, verified: true }; })), '/settings/security');
    await until(() => expect(byText(host, 'button', 'Make new codes')).not.toBeNull());
    expect(text(host.querySelector('.set-sec:nth-of-type(2) .set-row p'))).toContain('from a browser. 3 backup codes left.');
    click(byText(host, 'button', 'Make new codes'));
    expect(document.querySelector('#ra-password')).toBeNull();
    click(byText(document.body, '.modal button', 'Make new codes'));
    expect(text(document.querySelector('.modal .err'))).toBe('Enter a code from your app, or a backup code.');
    typeInto(document.querySelector<HTMLInputElement>('#ra-code')!, '123456');
    click(byText(document.body, '.modal button', 'Make new codes'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Backup codes'));
    expect(requests(sent, 'POST', /codes$/)[0].body).toEqual({ code: '123456' });
  });

  it('asks for a code with a new password when two-step sign-in is on, and offers to revoke API keys', async () => {
    const { host, sent } = start(routes([['POST', /\/api\/_settings\/me\/password$/, { status: 200, etag: null, body: { changed: true, revoked_keys: 2 } }]],
      me(body => { body.security.two_step = { on: true, since: 1793534400, agent: 'a browser', codes: 3, required: false, available: true, verified: true }; body.security.api_keys = 2; })),
      '/settings/security');
    await until(() => expect(byText(host, 'button', 'Change password')).not.toBeNull());
    click(byText(host, 'button', 'Change password'));
    const box = byText(host, '[role="checkbox"]', 'Also revoke API keys (2 active)')!;
    expect(box.getAttribute('aria-checked')).toBe('false');
    typeInto(host.querySelector<HTMLInputElement>('#pw-cur')!, 'old password here');
    typeInto(host.querySelector<HTMLInputElement>('#pw-new')!, 'a brand new long password');
    typeInto(host.querySelector<HTMLInputElement>('#pw-again')!, 'a brand new long password');
    click(byText(host, 'button', 'Update password'));
    await until(() => expect(text(host)).toContain('Enter a code from your app, or a backup code.'));
    typeInto(host.querySelector<HTMLInputElement>('#pw-code')!, '123456');
    click(box);
    click(byText(host, 'button', 'Update password'));
    await until(() => expect(requests(sent, 'POST', /password$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /password$/)[0].body).toEqual({ current: 'old password here', new: 'a brand new long password', code: '123456', revoke_keys: true });
    await until(() => expect(lastToast()).toBe('Password changed. Other devices were signed out and your API keys revoked.'));
  });

  it('cannot turn two-step sign-in off when the role requires it', async () => {
    const { host } = start(routes([], me(body => { body.security.two_step = { on: true, since: 1793534400, agent: 'a browser', codes: 8, required: true, available: true, verified: true }; })), '/settings/security');
    await until(() => expect(byText(host, 'button', 'Turn off')).not.toBeNull());
    expect((byText(host, 'button', 'Turn off') as HTMLButtonElement).disabled).toBe(true);
    expect(text(host.querySelector('.set-sec:nth-of-type(2) .set-sec-h p'))).toBe('Required for the admin role in this workspace.');
  });

  it('finds any setting by its label, app settings included', async () => {
    const { host } = start(routes(), '/settings/profile');
    await until(() => expect(host.querySelector('.set-search input')).not.toBeNull());
    typeInto(host.querySelector<HTMLInputElement>('.set-search input')!, 'payment');
    await until(() => expect(text(host.querySelector('.set-nav-item.hit'))).toBe('Sales: Default payment termsSales settings'));
    click(host.querySelector('.set-nav-item.hit'));
    await until(() => expect(location.pathname).toBe('/settings/app/sales'));
    typeInto(host.querySelector<HTMLInputElement>('.set-search input')!, 'nothing like this');
    await until(() => expect(text(host.querySelector('.set-none'))).toBe('No setting matches.'));
  });
});

describe('connections', () => {
  it('shows the personal API keys page in the Settings shell', async () => {
    const { host, sent } = start(routes([['GET', /\/api\/_settings\/me\/keys$/, { status: 200, etag: null, body: { items: [], models: [{ name: 'Customer', label: 'Customer' }] } }]]), '/settings/keys');
    await until(() => expect(text(host)).toContain("No keys. Most people don't need one."));
    expect(text(host.querySelector('.set-nav-item.on'))).toBe('API keys');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('API keys');
    expect(requests(sent, 'GET', /\/api\/_settings\/me\/keys$/)).toHaveLength(1);
  });

  it('shows remote assistants and keeps the local evlet mcp steps on the AI page', async () => {
    const ai = { enabled: true, writes: false, address: 'http://127.0.0.1:8000/_/mcp', clients: [], requests: [], apps: ['sales'], chosen: null };
    const { host } = start(routes([['GET', /\/api\/_settings\/me\/ai$/, { status: 200, etag: null, body: ai }]]), '/settings/ai');
    await until(() => expect(text(host)).toContain('http://127.0.0.1:8000/_/mcp'));
    expect(text(host)).toContain('Add custom connector');
    expect(text(host)).toContain('evlet mcp --session ~/.evlet/session.json');
    expect(text(host)).toContain('evlet login --url');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('AI');
  });

  it('shows the personal mailbox page in the Settings shell', async () => {
    const email = { allowed: true, providers: { microsoft: true, google: false }, shared: 'noreply@acme.test', mailbox: null };
    const { host, sent } = start(routes([['GET', /\/api\/_settings\/me\/mailbox$/, { status: 200, etag: null, body: email }]]), '/settings/email');
    await until(() => expect(text(host)).toContain('Microsoft 365'));
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Your email');
    expect(requests(sent, 'GET', /\/api\/_settings\/me\/mailbox$/)).toHaveLength(1);
  });
});

describe('app settings', () => {
  it('saves shared settings through the save bar and shows the default with a reset', async () => {
    const { host, sent } = start(routes([['GET', /\/api\/_settings\/app\/sales$/, screen.settings_app],
      ['PATCH', /\/api\/_settings\/app\/sales$/, screen.settings_app_saved]]), '/settings/app/sales');
    await until(() => expect(text(host.querySelector('.set-head h1'))).toBe('Sales settings'));
    expect([...host.querySelectorAll('.set-sec-h h2')].map(text)).toEqual(['Orders', 'Work week']);
    const terms = host.querySelector<HTMLInputElement>('input[aria-label="Default payment terms"]')!;
    expect(terms.closest('.set-affix')!.querySelector('.suf')!.textContent).toBe('days');
    typeInto(terms, '45');
    await until(() => expect(text(host.querySelector('.set-bar strong'))).toBe('1 unsaved change'));
    expect(text(host.querySelector('.set-default'))).toBe('Default: 30 days');
    click(byText(host, '.set-multi .chip', 'Fri'));
    await until(() => expect(text(host.querySelector('.set-bar strong'))).toBe('2 unsaved changes'));
    click(byText(host, '.set-bar button', 'Save for everyone'));
    await until(() => expect(lastToast()).toBe('2 settings saved for everyone'));
    expect(requests(sent, 'PATCH', /app\/sales$/)[0].body).toEqual({ values: { terms: 45, days: ['mon', 'tue', 'wed', 'thu'] } });
    expect(host.querySelector('.set-bar')).toBeNull();
    click(byText(host, '.tabs button', 'Just for you'));
    expect(text(host.querySelector('.set-callout'))).toBe('Only for you. These save as you change them.');
  });

  it('shows shared settings read only with the lock note to people who cannot change them', async () => {
    const { host } = start(routes([['GET', /\/api\/_settings\/app\/sales$/, screen.viewer_settings_app]],
      me(body => { body.apps[0].can_edit = false; })), '/settings/app/sales');
    await until(() => expect(host.querySelector('.set-callout.warn')).not.toBeNull());
    expect(text(host.querySelector('.set-callout.warn'))).toBe('You can see these settings but not change them. They apply to everyone using Sales. Ask someone with the editor role or an admin.');
    expect(host.querySelector<HTMLInputElement>('input[aria-label="Default payment terms"]')!.disabled).toBe(true);
    expect(host.querySelector('.set-nav-item.on .set-lock')).not.toBeNull();
  });
});

describe('two-step sign-in', () => {
  it('asks for a code after the password and signs in with it', async () => {
    let signedIn = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => signedIn ? recorded.session : recorded.session_anonymous],
      ['POST', /\/api\/auth\/login$/, sent => sent.body.ticket
        ? (sent.body.code === '654321' ? (signedIn = true, recorded.login) : { status: 401, etag: null, body: { error: 'That code is not right. Try again.' } })
        : { status: 200, etag: null, body: { two_step: 'code', ticket: 'fixture-ticket' } }],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/login');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Welcome back'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'admin');
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'a sufficiently long password');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await until(() => expect(text(host.querySelector('h1'))).toBe('Two-step sign-in'));
    typeInto(host.querySelector<HTMLInputElement>('#tfa-code')!, '111111');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('That code is not right. Try again.'));
    typeInto(host.querySelector<HTMLInputElement>('#tfa-code')!, '654321');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(requests(sent, 'POST', /login$/).map(item => item.body)).toEqual([{ username: 'admin', password: 'a sufficiently long password' },
      { ticket: 'fixture-ticket', code: '111111' }, { ticket: 'fixture-ticket', code: '654321' }]);
  });
});
