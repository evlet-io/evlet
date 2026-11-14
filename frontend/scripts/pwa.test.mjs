import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(`${process.cwd()}/public/assets/evlet-sw.js`, 'utf8');
function worker(fetch = vi.fn(async () => new Response('public asset'))) {
  const listeners = {}, entries = new Map([['/assets/evlet-cache-state', new Response('enabled')]]);
  const cache = {
    match: vi.fn(async key => entries.get(typeof key === 'string' ? key : key.url)?.clone()),
    put: vi.fn(async (key, response) => entries.set(typeof key === 'string' ? key : key.url, response)),
    keys: vi.fn(async () => [...entries.keys()].map(url => ({ url: new URL(url, 'https://evlet.test').href }))),
    delete: vi.fn(async key => entries.delete(typeof key === 'string' ? key : key.url)),
  };
  const caches = { open: vi.fn(async () => cache), match: cache.match, keys: vi.fn(async () => ['evlet-static-v1', 'another-app']), delete: vi.fn() };
  vm.runInNewContext(source, { URL, Response, fetch, caches, self: { location: { origin: 'https://evlet.test' }, addEventListener: (name, run) => { listeners[name] = run; } } });
  const request = (path, options = {}) => {
    let response;
    listeners.fetch({ request: { url: new URL(path, 'https://evlet.test').href, method: 'GET', mode: 'cors', ...options }, respondWith: value => { response = value; } });
    return response;
  };
  return { request, listeners, entries, cache, caches, fetch };
}

describe('opt-in PWA static cache', () => {
  it('never intercepts API calls, mutations, external assets or ordinary HTML files', () => {
    const w = worker();
    for (const [path, options] of [
      ['/api/customer', {}], ['/api/_settings/me', {}], ['/assets/user.html', {}], ['/custom-site', { mode: 'navigate' }],
      ['/assets/app-12345678.js?private=1', {}], ['https://external.test/assets/app-12345678.js', {}],
      ['/api/customer', { method: 'POST' }], ['/assets/app-12345678.js', { method: 'PATCH' }],
    ]) expect(w.request(path, options)).toBeUndefined();
    expect(w.fetch).not.toHaveBeenCalled(); expect(w.cache.put).not.toHaveBeenCalled();
  });

  it('fetches static assets without credentials and does not turn quota failure into a network failure', async () => {
    const w = worker();
    w.cache.put.mockRejectedValueOnce(new Error('quota exceeded'));
    const response = await w.request('/assets/app-12345678.js');
    expect(await response.text()).toBe('public asset');
    expect(w.fetch).toHaveBeenCalledExactlyOnceWith('https://evlet.test/assets/app-12345678.js', { credentials: 'omit' });
  });

  it('never caches online navigation responses and uses only the public notice on network failure', async () => {
    const fetch = vi.fn(async () => new Response('authenticated HTML'));
    const w = worker(fetch);
    expect(await (await w.request('/app/customer', { mode: 'navigate' })).text()).toBe('authenticated HTML');
    expect(w.cache.put).not.toHaveBeenCalled();
    w.entries.set('/assets/evlet-offline.html', new Response('generic offline notice'));
    fetch.mockRejectedValueOnce(new Error('offline'));
    expect(await (await w.request('/app/customer', { mode: 'navigate' })).text()).toBe('generic offline notice');
    expect(w.cache.put).not.toHaveBeenCalled();
  });

  it('checks the durable opt-out marker when a worker starts again', async () => {
    const w = worker();
    w.entries.set('/assets/evlet-cache-state', new Response('disabled'));
    await w.request('/assets/app-12345678.js');
    expect(w.cache.put).not.toHaveBeenCalled();
    expect(w.fetch.mock.calls[0][0]).toMatchObject({ url: 'https://evlet.test/assets/app-12345678.js' });
  });

  it('disables interception immediately and removes only Evlet caches', async () => {
    const w = worker();
    let pending;
    w.listeners.message({ data: { type: 'EVLET_DISABLE_STATIC_CACHE' }, waitUntil: value => { pending = value; } });
    await pending;
    expect(w.request('/assets/app-12345678.js')).toBeUndefined();
    expect(w.caches.delete).toHaveBeenCalledExactlyOnceWith('evlet-static-v1');
    expect(source).not.toMatch(/\bskipWaiting\s*\(|\bclients\.claim\s*\(/);
  });
});
