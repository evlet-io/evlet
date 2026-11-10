import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';
import { closeDialogs } from '../ui/layers';
import { Icon } from '../ui/base';

afterEach(stopAll);

// Vitest hands CSS imports over empty, so the stylesheet is read from disk (Vitest runs from frontend/).
const node = (globalThis as any).process;
const css = (path: string): string => node.getBuiltinModule('node:fs').readFileSync(`${node.cwd()}/src/evlet/${path}`, 'utf8');
/** The declarations of the first rule whose selector is exactly `selector`. */
const rule = (sheet: string, selector: string): string => {
  const found = new RegExp(`(?:^|[}\\s])${selector.replace(/[.*+?^${}()|[\]\\>]/g, '\\$&')}\\{([^}]*)\\}`, 'm').exec(sheet);
  if (!found) throw new Error(`No ${selector} rule`);
  return found[1];
};

const submit = (host: HTMLElement) => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

describe('sign in, setup and Docs', () => {
  it('signs in from the login screen, lands on the first list, and opens Docs from the sidebar', async () => {
    let signedIn = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => signedIn ? recorded.session : recorded.session_anonymous],
      ['POST', /\/api\/auth\/login$/, sent => sent.body.password === 'right password' ? (signedIn = true, recorded.login) : recorded.login_failed],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/');
    await until(() => expect(host.querySelector('h1')?.textContent).toBe('Welcome back'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'admin');
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'wrong');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('Invalid username or password'));
    expect(host.querySelector('#ev-password')!.getAttribute('autocomplete')).toBe('current-password');

    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'right password');
    submit(host);
    // Docs are on in development, yet sign-in lands on the first list; Docs stay one click away.
    await until(() => expect(location.pathname).toBe('/app/customer'));
    await until(() => expect(text(host.querySelector('.nav-item.active'))).toBe('Customers'));
    click(byText(host, '.nav-item', 'Docs'));
    await until(() => expect(location.pathname).toBe('/docs'));
    expect(text(host.querySelector('.nav-item.active'))).toBe('Docs');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Build with Evlet'));
    const login = requests(sent, 'POST', /\/api\/auth\/login$/).pop()!;
    expect(login.body).toEqual({ username: 'admin', password: 'right password' });
    expect(login.headers['x-evlet-request']).toBe('1');
    // The aside lists every root model the role can read, linking to its list; Table rows are not listed.
    const links = [...host.querySelectorAll<HTMLAnchorElement>('.ev-doc-aside li a')].map(link => [text(link), link.getAttribute('href')]);
    expect(links).toEqual([['Customers', '/app/customer'], ['Products', '/app/product'], ['Orders', '/app/order'], ['Contacts', '/app/contact'], ['Quotes', '/app/quote']]);
  });

  it('first-run setup creates the administrator and signs in with it', async () => {
    let ready = false;
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, () => ready ? recorded.session : screen.session_setup],
      ['POST', /\/api\/auth\/setup$/, () => { ready = true; return screen.setup; }],
      ['POST', /\/api\/auth\/login$/, recorded.login],
      ['GET', /\/api\/schema$/, screen.schema],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
    ], '/login');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set up Evlet'));
    expect(text(host.querySelector('.ev-login-card > p'))).toBe('Create the first administrator account.');
    const password = host.querySelector<HTMLInputElement>('#ev-password')!;
    expect(password.getAttribute('minlength')).toBe('15');
    expect(password.getAttribute('autocomplete')).toBe('new-password');
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'owner');
    typeInto(password, 'a sufficiently long password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/app/customer'));
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
      ['GET', /\/api\/contact\/0000000000001$/, screen.contact_detail],
      ['GET', /\/api\/contact\/0000000000001\/attachments$/, screen.attachments_empty],
    ], '/app/contact/0000000000001/view');
    await until(() => expect([...host.querySelectorAll('.ev-cmp-card .ff')].map(item => item.getAttribute('data-field'))).toEqual(['email', 'phone', 'notes']));
    expect(location.pathname).toBe('/app/contact/0000000000001/view');
  });

  it.each([['/reports/sales', 'This page is not available']])(
    '%s, a report the role cannot run, is not available', async (path, title) => {
      const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], path);
      await until(() => expect(text(host.querySelector('.page .empty h3'))).toBe(title));
      expect(byText(host, '.page button', 'Open Docs')).toBeFalsy();
      click(byText(host, '.page button', 'Open Customers'));
      await until(() => expect(location.pathname).toBe('/app/customer'));
    });

  it.each(['/demo/projects', '/', '/app'])('%s, the root or an address that matches no route, opens the first list', async path => {
    start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], path);
    await until(() => expect(location.pathname).toBe('/app/customer'));
  });
});

describe('app shell', () => {
  it('groups models in the sidebar, marks the current one and has no New menu in the top bar', async () => {
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], '/app/customer');
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    // Models without an app in their resource id form one workspace, with the earlier groups as its sections.
    const groups = [...host.querySelectorAll('.nav-sec')].map(group => [text(group.querySelector('.nav-sec-h .lbl')), [...group.querySelectorAll('.nav-item .lbl')].map(text)]);
    expect(groups).toEqual([['Documents', ['Orders']], ['Records', ['Customers', 'Products', 'Contacts', 'Quotes']]]);
    expect(text(host.querySelector('.nav-item.active'))).toBe('Customers');
    expect(host.querySelector('.nav-item.active')!.getAttribute('aria-current')).toBe('page');
    // Library is a development link outside the router.
    expect(byText(host, '.side-bottom a', 'Library')!.getAttribute('rel')).toBe('external');
    // New records start from the list's own New button; the top bar has none.
    expect(byText(host, '.topbar button', 'New')).toBeNull();
    expect(host.querySelector('.topbar .btn.primary')).toBeNull();
  });

  it('shows the Evlet brand, a pipe and the Workspace switcher at the left of the top bar, and collapses from the edge button', async () => {
    const { host } = start([...adminRoutes()]);
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    const app = host.querySelector('.app')!;
    // Left to right: the menu button (narrow windows), then the brand, a pipe and the switcher, then the actions on the right.
    const left = host.querySelector<HTMLElement>('.topbar > .top-left')!;
    expect(left.previousElementSibling!.classList.contains('nav-menu-btn')).toBe(true);
    expect(left.nextElementSibling!.classList.contains('top-actions')).toBe(true);
    expect([...left.children].map(child => child.className)).toEqual(['brand', 'top-pipe', 'app-switch']);
    // The evlet.io lockup for light and dark themes, and the mark for the collapsed sidebar, as on main.
    const brand = [...left.querySelectorAll<HTMLImageElement>('.brand img')].map(img => [img.className, img.getAttribute('src'), img.alt]);
    expect(brand).toEqual([['ev-lockup-light', '/assets/evlet-logo-primary.svg', 'Evlet'],
      ['ev-lockup-dark', '/assets/evlet-logo-white.svg', 'Evlet'], ['ev-logo', '/assets/evlet-icon-blue.svg', 'Evlet']]);
    expect(left.querySelector('.top-pipe')!.getAttribute('aria-hidden')).toBe('true');
    // The sidebar's head holds the same brand and a second switcher, shown only when the sidebar is a drawer
    // (ui/styles.css); its body starts with the navigation.
    const sidebar = host.querySelector('.sidebar')!;
    const head = sidebar.querySelector('.side-head')!;
    expect(sidebar.firstElementChild).toBe(head);
    expect([...head.children].map(child => child.className)).toEqual(['brand', 'app-switch']);
    expect(head.querySelector('.brand')!.innerHTML).toBe(left.querySelector('.brand')!.innerHTML);
    expect(sidebar.querySelector('.side-body .brand, .side-body img, .side-body .app-switch')).toBeNull();
    expect(host.querySelectorAll('.ev-logo')).toHaveLength(2);
    expect(sidebar.querySelector('.side-body')!.firstElementChild!.classList.contains('nav')).toBe(true);
    // The switcher: the workspace name and a small chevron, and no site name after it.
    const appSwitch = left.querySelector<HTMLElement>('.app-switch')!;
    const appName = text(appSwitch.querySelector('.as-text b'));
    expect(appName).not.toBe('');
    expect(appSwitch.querySelector('.as-text small')).toBeNull();
    // The switch icon: the up-down chevrons, as on the account card, not a dropdown chevron.
    expect(appSwitch.querySelector('.as-chev')!.innerHTML).toBe(host.querySelector('.me-chev')!.innerHTML);
    expect(appSwitch.getAttribute('aria-label')).toBe(`Workspace: ${appName}`);
    // The old app bar and repeated app title are gone.
    expect(host.querySelector('.appbar, .side-app, .sidebar .ws')).toBeNull();
    click(appSwitch);
    const pop = document.querySelector('.pop.ws-pop')!;
    expect(pop.getAttribute('role')).toBe('menu');
    expect(pop.getAttribute('aria-label')).toBe('Workspace');
    expect([...pop.querySelectorAll('.ws-item')].map(text)).toEqual([appName]);
    expect(pop.querySelector('.ws-item.on')!.getAttribute('aria-checked')).toBe('true');
    closeDialogs();
    // The edge button collapses and expands; the top bar's toggle is only for the phone drawer. Collapsed,
    // the brand and the switcher stay in the top bar.
    const edge = host.querySelector<HTMLButtonElement>('.side-edge')!;
    expect(edge.getAttribute('aria-expanded')).toBe('true');
    click(edge);
    expect(app.classList.contains('collapsed')).toBe(true);
    expect(edge.getAttribute('aria-label')).toBe('Expand sidebar');
    expect(host.querySelector('.topbar > .top-left .brand .ev-logo')).not.toBeNull();
    expect(host.querySelector('.topbar > .top-left .app-switch')).toBe(appSwitch);
    click(edge);
    expect(app.classList.contains('collapsed')).toBe(false);
    expect(host.querySelector('.topbar > .nav-menu-btn button')).not.toBeNull();
  });

  it('turns the sidebar and switcher into a drawer from the menu button on narrow windows', async () => {
    const media = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes('max-width: 1220px'), media: query, addEventListener() {}, removeEventListener() {} })) as any;
    try {
      const { host } = start([...adminRoutes()], '/app/customer', { storage: localStorage });
      await until(() => expect(host.querySelector('.app')).not.toBeNull());
      const app = host.querySelector('.app')!;
      const button = host.querySelector<HTMLButtonElement>('.topbar > .nav-menu-btn button')!;
      expect(button.getAttribute('aria-label')).toBe('Menu');
      expect(button.getAttribute('aria-controls')).toBe('ev-sidebar');
      expect(button.getAttribute('aria-expanded')).toBe('false');
      // The menu icon (three lines), not the sidebar panel icon the record's side drawer button uses.
      expect(button.innerHTML).toBe(Icon('menu', { size: 'sm' }).outerHTML);
      click(button);
      expect(app.classList.contains('mobile-open')).toBe(true);
      expect(button.getAttribute('aria-expanded')).toBe('true');
      // The drawer: the brand at its top, the switcher, then the navigation.
      const sidebar = host.querySelector('.sidebar')!;
      expect([...sidebar.querySelector('.side-head')!.children].map(child => child.className)).toEqual(['brand', 'app-switch']);
      expect(sidebar.querySelector('.side-body .nav .nav-item')).not.toBeNull();
      // Esc closes it; so do the scrim and opening a page.
      press(document.body, 'Escape');
      expect(app.classList.contains('mobile-open')).toBe(false);
      click(button);
      click(host.querySelector('.ev-side-scrim'));
      expect(app.classList.contains('mobile-open')).toBe(false);
      click(button);
      click(byText(host, '.nav-item', 'Docs'));
      await until(() => expect(app.classList.contains('mobile-open')).toBe(false));
      // [ and the menu open the drawer; the collapsed rail is for wide windows only.
      press(document.body, '[');
      expect(app.classList.contains('mobile-open')).toBe(true);
      expect(app.classList.contains('collapsed')).toBe(false);
    } finally { window.matchMedia = media; }
  });

  it('styles the top bar left: brand, pipe and a compact switcher, collapsed and on phones', () => {
    const styles = css('ui/styles.css');
    // As wide as the expanded sidebar, so the breadcrumb starts over the main column; only as wide as it needs collapsed.
    expect(rule(styles, '.top-left')).toContain('width:calc(var(--sidebar-w) - 16px)');
    expect(rule(styles, '.app.collapsed .top-left')).toContain('width:auto');
    // The brand at main's size: the 26 px lockup, the 26 px mark hidden until collapsed; no filter or tile.
    expect(rule(styles, '.brand .ev-lockup')).toContain('height:26px');
    expect(rule(styles, '.brand .ev-logo')).toMatch(/display:none;width:26px;height:26px/);
    expect(styles).not.toMatch(/ev-logo[^{]*\{[^}]*filter/);
    // The pipe: one pixel wide, 16 px tall, in the border colour.
    expect(rule(styles, '.top-pipe')).toMatch(/width:1px;height:16px;background:var\(--border-strong\)/);
    // The switcher: a flat bordered button as tall as the search box, in the normal text colour, with the
    // solid focus outline; a light background on hover, and a stronger border while its menu is open.
    expect(rule(styles, '.app-switch')).toMatch(/height:32px;.*border:1px solid var\(--border\);border-radius:var\(--r\)/);
    expect(rule(styles, '.app-switch')).toContain('color:var(--fg)');
    expect(rule(styles, '.app-switch')).not.toContain('box-shadow');
    expect(rule(styles, '.top-search')).toContain('height:32px');
    expect(rule(styles, '.app-switch:hover')).toContain('background:var(--bg-muted)');
    expect(rule(styles, '.app-switch[aria-expanded="true"]')).toContain('border-color:var(--border-strong)');
    expect(rule(styles, '.app-switch:focus-visible')).toContain('outline:2px solid var(--accent)');
    // The Workspace menu: as wide as the other menus, flat (the light shadow), and no tile styles left.
    expect(rule(styles, '.ws-pop')).toContain('min-width:200px');
    expect(rule(styles, '.ws-pop')).toContain('box-shadow:var(--shadow-md)');
    expect(styles).not.toContain('.ws-tile');
    // Collapsed: the mark alone, the switcher compact.
    expect(rule(styles, '.app.collapsed .brand .ev-lockup')).toContain('display:none');
    expect(rule(styles, '.app.collapsed .brand .ev-logo')).toContain('display:block');
    // 1220 px and under: one column that never scrolls sideways; the top bar's brand and switcher give way to the
    // menu button, and the drawer's head shows the lockup as the top bar does at full width.
    expect(rule(styles, '.nav-menu-btn')).toContain('display:none');
    expect(rule(styles, '.side-head')).toContain('display:none');
    const narrow = styles.slice(styles.indexOf('@media (max-width:1220px){\n  .app,.app.collapsed'));
    expect(narrow).toContain('.app,.app.collapsed{grid-template-columns:minmax(0,1fr)}');
    expect(narrow).toContain('.nav-menu-btn{display:grid');
    expect(narrow).toContain('.top-left,.top-pipe{display:none}');
    expect(narrow).toContain('.side-edge{display:none}');
    expect(narrow).toContain('.side-head .brand .ev-lockup{display:block}');
    expect(narrow).toContain('.side-head .brand .ev-logo{display:none}');
    // The record's side column is 370 px; list and record pages share one side gutter, and a list's measure cards
    // are as far from the title and the list card as from each other.
    const screens = css('app/screens.css');
    expect(styles).toContain('--doc-side-w:370px;');
    expect(styles).toMatch(/--page-gutter:12px; --page-gap:8px;/);
    expect(rule(styles, '.doc')).toContain('grid-template-columns:minmax(0,1fr) var(--doc-side-w)');
    expect(rule(styles, '.doc-main')).toContain('padding:22px var(--page-gutter) 96px');
    expect(rule(screens, '.ev-list.page')).toContain('padding:var(--ev-pad-top) var(--page-gutter) 16px');
    expect(rule(screens, '.ev-list .sum')).toBe('gap:var(--page-gap);margin:0 0 var(--page-gap)');
    expect(rule(screens, '.ev-list>.ph:has(+.sum)')).toBe('margin-bottom:var(--page-gap)');
  });

  it('toggles the sidebar with [ and signs out from the account menu', async () => {
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
    // Without the person's settings (an older server) the page follows the device and has no theme button.
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(host.querySelector('.side-foot .theme-btn')).toBeNull();
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
    const { host } = start([['GET', /\/api\/auth\/session$/, session], ['GET', /\/api\/schema$/, screen.schema], ['GET', /\/api\/customer\?/, screen.customers_page1]], '/');
    await until(() => expect(location.pathname).toBe('/app/customer'));
    await until(() => expect(byText(host, '.nav-item', 'Docs')).toBeTruthy());
  });
});

describe('the start page after sign-in', () => {
  const me = (start: string) => { const value = copy(screen.settings_me); (value.body as any).prefs.start = start; return value; };
  const lists: Route[] = [['GET', /\/api\/(customer|order)\?/, screen.customers_page1]];
  // A new app with no models yet: an empty schema.
  const empty = () => { const value = copy(screen.schema); (value.body as any).models = []; return value; };
  const off = () => { const session = copy(recorded.session); (session.body as any).application.docs = null; return session; };

  it('opens the start page saved in Settings: a list, or Docs', async () => {
    start([...adminRoutes(), ...lists, ['GET', /\/api\/_settings\/me$/, me('order')]], '/');
    await until(() => expect(location.pathname).toBe('/app/order'));
    stopAll();
    start([...adminRoutes(), ...lists, ['GET', /\/api\/_settings\/me$/, me('docs')]], '/login');
    await until(() => expect(location.pathname).toBe('/docs'));
  });

  it('ignores a saved start page the role cannot open and opens the first list', async () => {
    start([...adminRoutes(), ...lists, ['GET', /\/api\/_settings\/me$/, me('ledger')]], '/');
    await until(() => expect(location.pathname).toBe('/app/customer'));
  });

  it('opens Docs only when the app has no list, and says so when Docs are off too', async () => {
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, empty()]], '/');
    await until(() => expect(location.pathname).toBe('/docs'));
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Build with Evlet'));
    stopAll();
    const second = start([['GET', /\/api\/auth\/session$/, off()], ['GET', /\/api\/schema$/, empty()]], '/');
    await until(() => expect(text(second.host.querySelector('.empty h3'))).toBe('Nothing to open yet'));
    expect(location.pathname).toBe('/');
  });
});
