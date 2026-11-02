import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
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
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).toEqual(['Your profile', 'Appearance', 'Sign-in & security', 'All settings', 'Controls↗', 'Sign out']);
    click(byText(document.body, '.pop [role="menuitem"]', 'All settings'));
    await until(() => expect(location.pathname).toBe('/settings/profile'));
    stopAll();
    const viewer = start(routes([], me(body => { body.controls = false; })), '/docs');
    await until(() => expect(viewer.host.querySelector('.me')).not.toBeNull());
    click(viewer.host.querySelector('.me'));
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).not.toContain('Controls↗');
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
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Confirmation link'));
    expect(requests(sent, 'POST', /email$/)[0].body).toEqual({ email: 'new@example.com' });
    // The last page is remembered on the server.
    expect(requests(sent, 'PATCH', /prefs$/).map(item => item.body)).toEqual([]);
  });

  it('applies the region everywhere: the date format changes the dates in a list', async () => {
    const { host, sent } = start(routes([['GET', /\/api\/order\?/, screen.orders_open]]), '/settings/appearance');
    await until(() => expect(text(host.querySelector('.set-head h1'))).toBe('Appearance & region'));
    const date = [...host.querySelectorAll<HTMLSelectElement>('select')].find(item => item.getAttribute('aria-label') === 'Date format')!;
    expect([...date.options].map(option => option.value)).toEqual(['d MMM yyyy', 'dd-MM-yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy']);
    date.value = 'yyyy-MM-dd';
    date.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(lastToast()).toBe('Saved'));
    expect(requests(sent, 'PATCH', /prefs$/).map(item => item.body)).toEqual([{ last_page: 'appearance' }, { date: 'yyyy-MM-dd' }]);
    click(byText(host, '.nav-item', 'Orders'));
    await until(() => expect(text(host.querySelector('tbody tr[data-key]'))).toContain('2026-10-20'));
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
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Set up two-step sign-in'));
    expect(text(document.querySelector('.set-key code'))).toBe('JBSW Y3DP EHPK 3PXP');
    typeInto(document.querySelector<HTMLInputElement>('#tfa-code')!, '123456');
    click(byText(document.body, '.modal button', 'Turn on'));
    await until(() => expect(text(document.querySelector('.modal h2'))).toBe('Backup codes'));
    expect([...document.querySelectorAll('.set-codes span')].map(text)).toEqual(['abcd-efgh', 'jkmn-pqrs']);
    expect(requests(sent, 'POST', /confirm$/)[0].body).toEqual({ code: '123456' });
  });

  it('cannot turn two-step sign-in off when the role requires it', async () => {
    const { host } = start(routes([], me(body => { body.security.two_step = { on: true, since: 1793534400, codes: 8, required: true, available: true }; })), '/settings/security');
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
    await until(() => expect(location.pathname).toBe('/docs'));
    expect(requests(sent, 'POST', /login$/).map(item => item.body)).toEqual([{ username: 'admin', password: 'a sufficiently long password' },
      { ticket: 'fixture-ticket', code: '111111' }, { ticket: 'fixture-ticket', code: '654321' }]);
  });
});
