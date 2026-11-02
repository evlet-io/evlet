import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';
import { closeDialogs } from '../ui/layers';

afterEach(stopAll);

const submit = (host: HTMLElement) => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

describe('sign in, setup and Docs', () => {
  it('signs in from the login screen, then opens Docs with the models of this workspace', async () => {
    let signedIn = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => signedIn ? recorded.session : recorded.session_anonymous],
      ['POST', /\/api\/auth\/login$/, sent => sent.body.password === 'right password' ? (signedIn = true, recorded.login) : recorded.login_failed],
      ['GET', /\/api\/schema$/, screen.schema],
    ], '/');
    await until(() => expect(host.querySelector('h1')?.textContent).toBe('Welcome back'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'admin');
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'wrong');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('Invalid username or password'));
    expect(host.querySelector('#ev-password')!.getAttribute('autocomplete')).toBe('current-password');

    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'right password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/docs'));
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Build with Evlet'));
    const login = requests(sent, 'POST', /\/api\/auth\/login$/).pop()!;
    expect(login.body).toEqual({ username: 'admin', password: 'right password' });
    expect(login.headers['x-evlet-request']).toBe('1');
    // The aside lists every root model the role can read, linking to its list; Table rows are not listed.
    const links = [...host.querySelectorAll<HTMLAnchorElement>('.ev-doc-aside li a')].map(link => [text(link), link.getAttribute('href')]);
    expect(links).toEqual([['Customers', '/app/customer'], ['Products', '/app/product'], ['Orders', '/app/order'], ['Contacts', '/app/contact']]);
  });

  it('first-run setup creates the administrator and signs in with it', async () => {
    let ready = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => ready ? recorded.session : screen.session_setup],
      ['POST', /\/api\/auth\/setup$/, () => { ready = true; return screen.setup; }],
      ['POST', /\/api\/auth\/login$/, recorded.login],
      ['GET', /\/api\/schema$/, screen.schema],
    ], '/login');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set up Evlet'));
    expect(text(host.querySelector('.ev-login-card > p'))).toBe('Create the first administrator account.');
    const password = host.querySelector<HTMLInputElement>('#ev-password')!;
    expect(password.getAttribute('minlength')).toBe('15');
    expect(password.getAttribute('autocomplete')).toBe('new-password');
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'owner');
    typeInto(password, 'a sufficiently long password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/docs'));
    const order = sent.filter(item => item.method === 'POST').map(item => new URL(item.url, 'http://x').pathname);
    expect(order).toEqual(['/api/auth/setup', '/api/auth/login']);
    expect(requests(sent, 'POST', /setup$/)[0].body).toEqual({ username: 'owner', password: 'a sufficiently long password' });
  });

  it('keeps a deep link through sign-in and opens Docs topics', async () => {
    const { host } = start(adminRoutes(), '/docs/screens');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Screens and routes'));
    expect(host.querySelector('.crumbs a')!.getAttribute('href')).toBe('/docs');
    expect(text(host.querySelector('.crumbs .cur'))).toBe('Screens and routes');
    click(byText(host, '.ev-docs .tabs a', 'Troubleshooting'));
    await until(() => expect(location.pathname).toBe('/docs/troubleshooting'));
    expect(text(host.querySelector('.ph h1'))).toBe('Troubleshooting');
    expect(byText(host, 'h2', 'A record changed before I saved')).not.toBeNull();
  });
});

describe('addresses the Python server serves', () => {
  it('opens a record from the older /app/model/id/view link', async () => {
    const { host } = start([...adminRoutes(),
      ['GET', /\/api\/contact\/1$/, screen.contact_detail],
      ['GET', /\/api\/contact\/1\/attachments$/, screen.attachments_empty],
    ], '/app/contact/1/view');
    await until(() => expect([...host.querySelectorAll('.fs .ff')].map(item => item.getAttribute('data-field'))).toEqual(['name', 'email', 'phone', 'notes']));
    expect(location.pathname).toBe('/app/contact/1/view');
  });

  it.each([['/reports/sales', 'Reports are not shown in this interface']])(
    '%s says the screen is not part of this interface', async (path, title) => {
      const { host } = start(adminRoutes(), path);
      await until(() => expect(text(host.querySelector('.page .empty h3'))).toBe(title));
      click(byText(host, '.page button', 'Open Docs'));
      await until(() => expect(location.pathname).toBe('/docs'));
    });

  it('an address that matches no route, such as a Python page, opens Docs', async () => {
    start(adminRoutes(), '/demo/projects');
    await until(() => expect(location.pathname).toBe('/docs'));
  });
});

describe('app shell', () => {
  it('groups models in the sidebar, marks the current one and offers New for creatable models', async () => {
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], '/app/customer');
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

  it('shows the Evlet brand, an app switcher with its workspace, and collapses from the edge button', async () => {
    const { host } = start([...adminRoutes()]);
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    const app = host.querySelector('.app')!;
    // The evlet.io artwork: the horizontal logo for each theme, and the mark the collapsed sidebar shows.
    const brand = [...host.querySelectorAll<HTMLImageElement>('.sidebar .brand img')].map(img => [img.className, img.getAttribute('src'), img.alt]);
    expect(brand).toEqual([['ev-lockup-light', '/assets/evlet-logo-primary.svg', 'Evlet'],
      ['ev-lockup-dark', '/assets/evlet-logo-white.svg', 'Evlet'], ['ev-logo', '/assets/evlet-icon-blue.svg', 'Evlet']]);
    const appSwitch = host.querySelector<HTMLElement>('.sidebar .app-switch')!;
    const appName = text(appSwitch.querySelector('.as-text b'));
    expect(appName).not.toBe('');
    expect(appSwitch.querySelector('.as-text small')!.textContent).not.toBe('');
    // The old app bar and repeated app title are gone.
    expect(host.querySelector('.appbar, .side-app, .sidebar .ws')).toBeNull();
    click(appSwitch);
    const pop = document.querySelector('.pop.app-pop')!;
    expect(pop.getAttribute('role')).toBe('dialog');
    expect([...pop.querySelectorAll('.app-tile')].map(text)).toEqual([appName]);
    expect(pop.querySelector('.app-tile.on')!.getAttribute('aria-current')).toBe('true');
    closeDialogs();
    // The edge button collapses and expands; the top bar's toggle is only for the phone drawer.
    const edge = host.querySelector<HTMLButtonElement>('.side-edge')!;
    expect(edge.getAttribute('aria-expanded')).toBe('true');
    click(edge);
    expect(app.classList.contains('collapsed')).toBe(true);
    expect(edge.getAttribute('aria-label')).toBe('Expand sidebar');
    click(edge);
    expect(app.classList.contains('collapsed')).toBe(false);
    expect(host.querySelector('.topbar > .mobile-only .icon-btn, .topbar > .mobile-only button')).not.toBeNull();
  });

  it('toggles the sidebar with [, keeps the light theme, and signs out from the account menu', async () => {
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
    // Light only for now; the top bar has no avatar or gear and the account area has no theme button.
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(host.querySelector('.side-foot .icon-btn')).toBeNull();
    expect(host.querySelector('.topbar .avatar')).toBeNull();

    click(host.querySelector('.me'));
    click(byText(document.body, '.pop [role="menuitem"]', 'Sign out'));
    await until(() => expect(text(host.querySelector('h1'))).toBe('Welcome back'));
    expect(requests(sent, 'POST', /logout$/)).toHaveLength(1);
    expect(location.pathname).toBe('/login');
  });

  it('shows the keyboard shortcuts with ? and a page that is not available', async () => {
    const { host } = start(adminRoutes(), '/app/order_lines');
    await until(() => expect(text(host.querySelector('.empty h3'))).toBe('This page is not available'));
    press(document.body, '?');
    expect(text(document.querySelector('.modal h2'))).toBe('Keyboard shortcuts');
    expect(text(document.querySelector('.ev-keys'))).toContain('Search and commands');
    expect(lastToast()).toBe('');
  });
});

describe('Docs turned off by the instance', () => {
  // A domain instance answers application.docs: null in its session.
  const off = () => { const session = copy(recorded.session); (session.body as any).application.docs = null; return session; };
  const routes = (): Route[] => [['GET', /\/api\/auth\/session$/, off()], ['GET', /\/api\/schema$/, screen.schema], ['GET', /\/api\/customer\?/, screen.customers_page1]];

  it('starts on the first list and offers no Docs link, tile or shortcut', async () => {
    const { host, router } = start(routes(), '/');
    await until(() => expect(location.pathname).toBe('/app/customer'));
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    expect(byText(host, '.nav-item', 'Docs')).toBeFalsy();
    press(document.body, 'g');
    press(document.body, 'd');
    expect(location.pathname).toBe('/app/customer');
    press(document.body, '?');
    expect(text(document.querySelector('.ev-keys'))).not.toContain('Go to Docs');
    closeDialogs();
    await router.go('docs', {});
    await until(() => expect(text(host.querySelector('.empty h3'))).toBe('This page is not available'));
    expect(byText(host, '.empty button', 'Open Docs')).toBeFalsy();
    click(byText(host, '.empty button', 'Open Customers'));
    await until(() => expect(location.pathname).toBe('/app/customer'));
  });

  it('opens the first list for a Python page route and after signing in at /login', async () => {
    start(routes(), '/demo/projects');
    await until(() => expect(location.pathname).toBe('/app/customer'));
    stopAll();
    start(routes(), '/login');
    await until(() => expect(location.pathname).toBe('/app/customer'));
  });

  it('keeps Docs when an older server sends no docs field', async () => {
    const session = copy(recorded.session);
    delete (session.body as any).application.docs;
    const { host } = start([['GET', /\/api\/auth\/session$/, session], ['GET', /\/api\/schema$/, screen.schema]], '/');
    await until(() => expect(location.pathname).toBe('/docs'));
    await until(() => expect(byText(host, '.nav-item', 'Docs')).toBeTruthy());
  });
});
