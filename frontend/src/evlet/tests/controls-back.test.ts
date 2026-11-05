// Getting between Controls and the app: Back to app in the Controls sidebar head goes to the last
// app page this browser remembers for the person, or the start page; the app's palette offers
// Go to Controls only to people allowed to open Controls.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { backToApp, rememberAppPage } from '../app/last-page';
import { server } from './server';
import { adminRoutes, press, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); localStorage.clear(); vi.restoreAllMocks(); });

const state = (apps: unknown[] = []) => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '0.3.0', version: 1, slug: null, managed: false },
  me: { id: 1, username: 'owner', role: 'admin' }, apps, jobs: { failed: 0 }, models: [], roles: [], scopes: {}, settings: {},
  controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0, users: { active: 1, total: 1 }, audit: [],
  two_step: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
});

function openControls(apps: unknown[] = []) {
  window.history.replaceState(null, '', '/controls/users');
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
    ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(apps), etag: null })],
    ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: { version: 1, problems: [], items: [] }, etag: null })],
    ['GET', /\/api\/_controls\/users/, () => ({ status: 200, etag: null, body: { offset: 0, limit: 100, total: 0, items: [] } })],
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); host.remove(); });
  return { host, router };
}
const backLink = (host: Element) => host.querySelector<HTMLAnchorElement>('.ctl-side > .ctl-side-head > a.ctl-back');

describe('the remembered app page', () => {
  it('keeps the last app path per person and falls back to the start page', () => {
    expect(backToApp('owner')).toEqual({ href: '/', app: null });
    rememberAppPage('owner', '/app/customer?q=acme', 'People');
    expect(backToApp('owner')).toEqual({ href: '/app/customer?q=acme', app: 'People' });
    expect(backToApp('rahul').href).toBe('/');
    // Only paths on this origin, outside Controls and the API, are kept.
    for (const path of ['//evil.example/app', '/\\evil.example', 'https://evil.example/', '/controls/users', '/api/customer', '/_/oauth/authorize'])
      rememberAppPage('owner', path, 'People');
    expect(backToApp('owner').href).toBe('/app/customer?q=acme');
    localStorage.setItem('evlet.last_app.owner', JSON.stringify({ path: '//evil.example', app: 'X' }));
    expect(backToApp('owner').href).toBe('/');
    localStorage.setItem('evlet.last_app.owner', 'not json');
    expect(backToApp('owner')).toEqual({ href: '/', app: null });
  });

  it('is written by the app shell as pages change', async () => {
    const { host, router } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }]] as Route[], '/docs');
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    const user = Object.keys(localStorage).find(key => key.startsWith('evlet.last_app.'))!.slice('evlet.last_app.'.length);
    expect(backToApp(user).href).toBe('/docs');
    await router.go('list', { model: 'customer' });
    await until(() => expect(backToApp(user).href).toBe('/app/customer'));
    expect(backToApp(user).app).toBeTruthy();
  });
});

describe('Back to app in Controls', () => {
  it('sits in the fixed sidebar head and goes to the start page when no app page is known', async () => {
    const { host } = openControls();
    await until(() => expect(backLink(host)).not.toBeNull());
    const link = backLink(host)!;
    expect(link.getAttribute('href')).toBe('/');
    expect(text(link)).toBe('Back to app');
    expect(link.querySelector('svg, .icon, [data-icon]')).not.toBeNull();
    // Head first, above the brand; the nav keeps scrolling alone.
    expect(link.parentElement!.firstElementChild).toBe(link);
    expect([...host.querySelector('.ctl-side')!.children].map(child => child.className)).toEqual(['ctl-side-head', 'ctl-nav', 'ctl-side-foot']);
  });

  it('goes to the last app page and names the app', async () => {
    rememberAppPage('owner', '/app/customer/12?tab=files', 'People');
    const { host } = openControls();
    await until(() => expect(backLink(host)).not.toBeNull());
    expect(backLink(host)!.getAttribute('href')).toBe('/app/customer/12?tab=files');
    expect(text(backLink(host))).toBe('Back to People');
  });

  it('names the installed app when the browser remembers nothing', async () => {
    const { host } = openControls([{ id: 'people', title: 'People', version: '1', source: '', models: [], connectors: [], permissions: 0, schedules: [],
      frontends: [{ name: 'evlet.desk', path: '/app', host: '', realm: 'desk' }], update: null, restart: false }]);
    await until(() => expect(backLink(host)).not.toBeNull());
    expect(text(backLink(host))).toBe('Back to People');
  });

  it('is a real link: the Controls router leaves plain and modified clicks to the browser', async () => {
    const { host, router } = openControls();
    await until(() => expect(backLink(host)).not.toBeNull());
    const link = backLink(host)!;
    expect(link.rel).toContain('external');
    for (const init of [{}, { metaKey: true }, { ctrlKey: true }]) {
      const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init });
      link.dispatchEvent(click);
      // Not prevented, so the browser follows the href: in place for a plain click, a new tab otherwise.
      expect(click.defaultPrevented).toBe(false);
      expect(router.route.peek()?.name).toBe('users');
    }
    // A page the app remembers after Controls opened is picked up on hover or focus.
    rememberAppPage('owner', '/reports', 'People');
    link.dispatchEvent(new Event('focus'));
    expect(link.getAttribute('href')).toBe('/reports');
  });
});

describe('Go to Controls in the palette', () => {
  const me = (controls: boolean) => { const value = copy(screen.settings_me); value.body.controls = controls; return value; };
  const routes = (controls: boolean): Route[] => [...adminRoutes(), ['GET', /\/api\/_settings\/me$/, me(controls)],
    ['GET', /\/api\/(customer|product|order|contact)\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 5, offset: 0 } }]];
  const palette = () => document.querySelector<HTMLElement>('.modal.palette');
  const pages = () => [...palette()!.querySelectorAll('.pal-sec[aria-label="Pages and actions"] .pal-it .lbl')].map(text);

  it('shows to people allowed to open Controls and opens it in a new tab', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue(null);
    const { host } = start(routes(true), '/docs');
    await until(() => expect(host.querySelector('.me .n')?.textContent).toBeTruthy());
    press(document.body, 'k', { ctrlKey: true });
    typeInto(palette()!.querySelector<HTMLInputElement>('.pal-in input')!, 'controls');
    await until(() => expect(pages()).toContain('Go to Controls'));
    press(palette()!.querySelector<HTMLInputElement>('.pal-in input')!, 'Enter');
    expect(opened).toHaveBeenCalledWith('/controls', '_blank', 'noopener');
  });

  it('is absent for people who cannot open Controls', async () => {
    const { host } = start(routes(false), '/docs');
    await until(() => expect(host.querySelector('.me .n')?.textContent).toBeTruthy());
    press(document.body, 'k', { ctrlKey: true });
    typeInto(palette()!.querySelector<HTMLInputElement>('.pal-in input')!, 'controls');
    await until(() => expect(palette()!.querySelector('.pal-list')!.textContent).toBeTruthy());
    expect(pages()).not.toContain('Go to Controls');
  });
});
