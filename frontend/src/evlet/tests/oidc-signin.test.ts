// OIDC fixture: real SQLite instance and loopback IdP, obtained through the signed callback
// and same-origin finish. Only random CSRF/ticket/secret values were normalized for replay.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '../api';
import { byText, click, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, server } from './server';
import oidc from './oidc-fixture.json';
import { rememberOidcNext, safeSignInNext, takeOidcNext } from '../app/signin-return';

afterEach(() => { stopAll(); vi.restoreAllMocks(); });
const anonymous = () => ({ status: 200, etag: null, body: copy(oidc.session) });
const finished = { status: 200, etag: null, body: oidc.finish };
const routes = (extra: Route[] = []): Route[] => [...extra,
  ['GET', /\/api\/auth\/session$/, anonymous()],
  ['GET', /\/api\/schema$/, screen.schema],
  ['GET', /\/api\/customer\?/, screen.customers_page1],
];

describe('company sign-in', () => {
  it('offers a full-navigation provider link alongside local login only when configured', async () => {
    const { host, sent } = start(routes(), '/login');
    await until(() => expect(byText(host, 'a', 'Company sign-in')).not.toBeNull());
    const link = byText<HTMLAnchorElement>(host, 'a', 'Company sign-in')!;
    expect(link.getAttribute('href')).toBe('/_/oidc/start'); expect(link.rel).toBe('external');
    expect(host.querySelector('#ev-username')).not.toBeNull(); expect(host.querySelector('#ev-password')).not.toBeNull();
    expect(requests(sent, 'POST', /oidc/)).toHaveLength(0);
    stopAll();
    const plain = start([['GET', /\/api\/auth\/session$/, recorded.session_anonymous]], '/login');
    await until(() => expect(plain.host.querySelector('#ev-username')).not.toBeNull());
    expect(byText(plain.host, 'a', 'Company sign-in')).toBeNull();
  });

  it('consumes the marker before requests and finishes on the bundled sign-in path', async () => {
    const path = '/login';
    const { host, api, sent } = start(routes([['POST', /\/_\/oidc\/finish$/, finished]]), `${path}?oidc=finish&user=sara`);
    expect(new URLSearchParams(location.search).has('oidc')).toBe(false);
    expect(new URLSearchParams(location.search).get('user')).toBe('sara');
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    const [finish] = requests(sent, 'POST', /oidc\/finish$/);
    expect(finish.body).toEqual({}); expect(finish.headers['content-type']).toBe('application/json');
    expect(finish.headers['x-evlet-request']).toBe('1'); expect(api.csrf).toBe(oidc.finish.csrf);
    expect(requests(sent, 'POST', /api\/auth\/login$/)).toHaveLength(0);
    expect(location.pathname).toBe('/app/customer');
  });

  it('leaves oidc query fields on other routes alone and never consumes a finish there', async () => {
    const { host, sent } = start(routes(), '/company-access?oidc=finish');
    await until(() => expect(host.querySelector('#ev-password')).not.toBeNull());
    expect(location.search).toBe('?oidc=finish');
    expect(requests(sent, 'POST', /oidc\/finish$/)).toHaveLength(0);
  });

  it('routes a provider MFA challenge through the existing second-step form', async () => {
    const { host, sent } = start(routes([
      ['POST', /\/_\/oidc\/finish$/, { status: 200, etag: null, body: oidc.challenge }],
      ['POST', /\/api\/auth\/login$/, recorded.login],
    ]), '/login?oidc=finish');
    await until(() => expect(text(host.querySelector('h1'))).toBe('Set up two-step sign-in'));
    typeInto(host.querySelector<HTMLInputElement>('#tfa-code')!, '123456');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await until(() => expect(host.querySelector('.app')).not.toBeNull());
    expect(requests(sent, 'POST', /auth\/login$/)[0].body).toEqual({ ticket: oidc.challenge.ticket, code: '123456' });
    expect(location.search).not.toContain('ticket');
  });

  it('shows a generic provider error and never replays finish when checking the connection', async () => {
    const { host, sent } = start(routes(), '/login?oidc=error');
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toContain('Company sign-in did not finish'));
    expect(location.search).toBe(''); expect(host.querySelector('#ev-password')).not.toBeNull();
    click(byText(host, 'button', 'Check connection'));
    await until(() => expect(requests(sent, 'GET', /auth\/session$/)).toHaveLength(2));
    expect(requests(sent, 'POST', /oidc\/finish$/)).toHaveLength(0);
  });

  it('treats an expired finish cookie as a sign-in failure without ending another session', async () => {
    const ended = vi.fn(); window.addEventListener('evlet-session-ended', ended);
    try {
      const { host, sent } = start(routes([['POST', /\/_\/oidc\/finish$/, { status: 401, etag: null, body: { error: 'Sign in again' } }]]), '/login?oidc=finish');
      await until(() => expect(text(host.querySelector('[role="alert"]'))).toContain('Company sign-in did not finish'));
      expect(ended).not.toHaveBeenCalled();
      click(byText(host, 'button', 'Check connection'));
      await until(() => expect(requests(sent, 'GET', /auth\/session$/)).toHaveLength(2));
      expect(requests(sent, 'POST', /oidc\/finish$/)).toHaveLength(1);
    } finally { window.removeEventListener('evlet-session-ended', ended); }
  });

  it('does not follow provider metadata pointing outside the supported same-origin start route', async () => {
    const value = anonymous(); value.body.oidc.start = 'https://untrusted.test/start';
    const { host } = start([['GET', /\/api\/auth\/session$/, value]], '/login');
    await until(() => expect(host.querySelector('#ev-password')).not.toBeNull());
    expect(byText(host, 'a', 'Company sign-in')).toBeNull();
  });

  it('uses same-origin credentials and leaves the finish path outside a custom API prefix', async () => {
    const stub = server([['POST', /^\/_\/oidc\/finish$/, finished]]), fetch = vi.fn(stub.fetch);
    const api = createClient({ fetch, api: '/custom-api' });
    await api.finishOidc();
    expect(fetch.mock.calls[0][0]).toBe('/_/oidc/finish');
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', body: '{}' });
    expect(api.csrf).toBe(oidc.finish.csrf);
  });
});


describe('provider return destination', () => {
  it('keeps the validated Controls destination through the provider and MFA round trip', async () => {
    const { host } = start(routes(), '/login?next=%2Fcontrols%2Fusers');
    await until(() => expect(byText(host, 'a', 'Company sign-in')).not.toBeNull());
    const link = byText(host, 'a', 'Company sign-in')!;
    link.addEventListener('click', event => event.preventDefault());
    click(link);
    const saved = sessionStorage.getItem('evlet.oidc-next');
    expect(saved).toContain('/controls/users');
    stopAll(); sessionStorage.setItem('evlet.oidc-next', saved!);
    const returned = start(routes([['POST', /\/_\/oidc\/finish$/, { status: 200, etag: null, body: oidc.challenge }]]), '/login?oidc=finish');
    expect(new URLSearchParams(location.search).get('next')).toBe('/controls/users');
    expect(sessionStorage.getItem('evlet.oidc-next')).toBeNull();
    await until(() => expect(text(returned.host.querySelector('h1'))).toBe('Set up two-step sign-in'));
    expect(new URLSearchParams(location.search).get('next')).toBe('/controls/users');
  });
  it('consumes each bounded hint once and rejects external, stale and oversized hints', () => {
    const consent = '/_/oauth/authorize?client_id=desktop&state=example';
    rememberOidcNext(consent); expect(takeOidcNext()).toBe(consent); expect(takeOidcNext()).toBeNull();
    for (const value of ['//foreign.test', 'https://foreign.test', '/controls/../other', '/controls\\foreign', '/_/oauth/authorize?' + 'x'.repeat(2048)]) {
      expect(safeSignInNext(value)).toBeNull(); rememberOidcNext(value); expect(takeOidcNext()).toBeNull();
    }
    for (const at of [Date.now() - 300001, Date.now() + 10000]) {
      sessionStorage.setItem('evlet.oidc-next', JSON.stringify({ next: '/controls', at }));
      expect(takeOidcNext()).toBeNull(); expect(sessionStorage.getItem('evlet.oidc-next')).toBeNull();
    }
  });
});
