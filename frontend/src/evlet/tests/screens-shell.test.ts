import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until } from './screens-helpers';

afterEach(stopAll);

const submit = (host: HTMLElement) => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

describe('sign in, setup and Docs', () => {
  it('signs in from the login screen, then opens Docs with the models of this workspace', async () => {
    let signedIn = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => signedIn ? recorded.session : recorded.session_anonymous],
      ['POST', /\/api\/auth\/login$/, sent => sent.body.password === 'right password' ? (signedIn = true, recorded.login) : recorded.login_failed],
      ['GET', /\/api\/schema$/, screen.schema],
    ], '/ui/');
    await until(() => expect(host.querySelector('h1')?.textContent).toBe('Welcome back'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'admin');
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'wrong');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('Invalid username or password'));
    expect(host.querySelector('#ev-password')!.getAttribute('autocomplete')).toBe('current-password');

    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'right password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/ui/docs'));
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Build with Evlet'));
    const login = requests(sent, 'POST', /\/api\/auth\/login$/).pop()!;
    expect(login.body).toEqual({ username: 'admin', password: 'right password' });
    expect(login.headers['x-evlet-request']).toBe('1');
    // The aside lists every root model the role can read, linking to its list; Table rows are not listed.
    const links = [...host.querySelectorAll<HTMLAnchorElement>('.ev-doc-aside li a')].map(link => [text(link), link.getAttribute('href')]);
    expect(links).toEqual([['Customers', '/ui/m/customer'], ['Products', '/ui/m/product'], ['Orders', '/ui/m/order'], ['Contacts', '/ui/m/contact']]);
  });

  it('first-run setup creates the administrator and signs in with it', async () => {
    let ready = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => ready ? recorded.session : screen.session_setup],
      ['POST', /\/api\/auth\/setup$/, () => { ready = true; return screen.setup; }],
      ['POST', /\/api\/auth\/login$/, recorded.login],
      ['GET', /\/api\/schema$/, screen.schema],
    ], '/ui/login');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set up Evlet'));
    expect(text(host.querySelector('.ev-login-card > p'))).toBe('Create the first administrator account.');
    const password = host.querySelector<HTMLInputElement>('#ev-password')!;
    expect(password.getAttribute('minlength')).toBe('15');
    expect(password.getAttribute('autocomplete')).toBe('new-password');
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'owner');
    typeInto(password, 'a sufficiently long password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/ui/docs'));
    const order = sent.filter(item => item.method === 'POST').map(item => new URL(item.url, 'http://x').pathname);
    expect(order).toEqual(['/api/auth/setup', '/api/auth/login']);
    expect(requests(sent, 'POST', /setup$/)[0].body).toEqual({ username: 'owner', password: 'a sufficiently long password' });
  });

  it('keeps a deep link through sign-in and opens Docs topics', async () => {
    const { host } = start(adminRoutes(), '/ui/docs/screens');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Screens and routes'));
    expect(host.querySelector('.crumbs a')!.getAttribute('href')).toBe('/ui/docs');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Screens and routes');
    click(byText(host, '.ev-docs .tabs a', 'Troubleshooting'));
    await until(() => expect(location.pathname).toBe('/ui/docs/troubleshooting'));
    expect(text(host.querySelector('.ph h1'))).toBe('Troubleshooting');
    expect(byText(host, 'h2', 'A record changed before I saved')).not.toBeNull();
  });
});

describe('app shell', () => {
  it('groups models in the sidebar, marks the current one and offers New for creatable models', async () => {
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], '/ui/m/customer');
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    const groups = [...host.querySelectorAll('.nav-group')].map(group => [text(group.querySelector('.nav-label')), [...group.querySelectorAll('.nav-item .lbl')].map(text)]);
    expect(groups).toEqual([['Documents', ['Orders']], ['Records', ['Customers', 'Products', 'Contacts']]]);
    expect(text(host.querySelector('.nav-item.active'))).toBe('Customers');
    expect(host.querySelector('.nav-item.active')!.getAttribute('aria-current')).toBe('page');
    // Library is a development link outside the router.
    expect(byText(host, '.side-bottom a', 'Library')!.getAttribute('rel')).toBe('external');
    click(byText(host, '.topbar button', 'New'));
    expect([...document.querySelectorAll('.pop [role="menuitem"]')].map(text)).toEqual(['New customer', 'New product', 'New order', 'New contact']);
  });

  it('toggles the sidebar with [, switches the theme and density, and signs out', async () => {
    let signedOut = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => signedOut ? recorded.session_anonymous : recorded.session],
      ['GET', /\/api\/schema$/, screen.schema],
      ['POST', /\/api\/auth\/logout$/, () => { signedOut = true; return recorded.logout; }],
    ]);
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    const app = host.querySelector('.app')!;
    press(document.body, '[');
    expect(app.classList.contains('collapsed')).toBe(true);
    press(document.body, '[');
    expect(app.classList.contains('collapsed')).toBe(false);

    click(host.querySelector('.side-foot .icon-btn'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    click(host.querySelector('.me'));
    click(byText(document.body, '.pop [role="menuitem"]', 'Compact density'));
    expect(app.classList.contains('compact')).toBe(true);

    click(host.querySelector('.me'));
    click(byText(document.body, '.pop [role="menuitem"]', 'Sign out'));
    await until(() => expect(text(host.querySelector('h1'))).toBe('Welcome back'));
    expect(requests(sent, 'POST', /logout$/)).toHaveLength(1);
    expect(location.pathname).toBe('/ui/login');
  });

  it('shows the keyboard shortcuts with ? and a page that is not available', async () => {
    const { host } = start(adminRoutes(), '/ui/m/order_lines');
    await until(() => expect(text(host.querySelector('.empty h3'))).toBe('This page is not available'));
    press(document.body, '?');
    expect(text(document.querySelector('.modal h2'))).toBe('Keyboard shortcuts');
    expect(text(document.querySelector('.ev-keys'))).toContain('Search and commands');
    expect(lastToast()).toBe('');
  });
});
