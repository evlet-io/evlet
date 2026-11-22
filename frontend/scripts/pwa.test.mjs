import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const original = readFileSync(`${process.cwd()}/public/assets/evlet-sw.js`, 'utf8');
// As vite build writes it (vite.config.js): this build's fingerprinted files.
const source = original.replace('[/* evlet:build-assets */]', JSON.stringify(['/assets/app-12345678.js', '/assets/index-Ab_c-123.css']))
  .replace("/* evlet:build */'dev'", "'v1'");
function worker(fetch = vi.fn(async () => new Response('public asset')), names = ['evlet-static-v1', 'another-app']) {
  const listeners = {}, entries = new Map([['/assets/evlet-cache-state', new Response('enabled')]]);
  // Like the Cache API, a relative key and its absolute URL name the same entry.
  const at = key => new URL(typeof key === 'string' ? key : key.url, 'https://evlet.test').pathname;
  const cache = {
    match: vi.fn(async key => entries.get(at(key))?.clone()),
    put: vi.fn(async (key, response) => entries.set(at(key), response)),
    keys: vi.fn(async () => [...entries.keys()].map(url => ({ url: new URL(url, 'https://evlet.test').href }))),
    delete: vi.fn(async key => entries.delete(at(key))),
  };
  const caches = { open: vi.fn(async () => cache), match: cache.match, keys: vi.fn(async () => names), delete: vi.fn() };
  vm.runInNewContext(source, { URL, Response, fetch, caches, self: { location: { origin: 'https://evlet.test' }, addEventListener: (name, run) => { listeners[name] = run; } } });
  const waits = [];
  const request = (path, options = {}) => {
    let response;
    listeners.fetch({ request: { url: new URL(path, 'https://evlet.test').href, method: 'GET', mode: 'cors', ...options },
      respondWith: value => { response = value; }, waitUntil: value => { waits.push(value); } });
    return response;
  };
  const message = async type => { let pending; listeners.message({ data: { type }, waitUntil: value => { pending = value; } }); await pending; };
  const settled = () => Promise.all(waits.splice(0));
  return { request, listeners, entries, cache, caches, fetch, message, settled };
}

describe('opt-in PWA static cache', () => {
  it('never intercepts API calls, mutations, external assets or ordinary HTML files', () => {
    const w = worker();
    for (const [path, options] of [
      ['/api/customer', {}], ['/api/_settings/me', {}], ['/assets/user.html', {}], ['/custom-site', { mode: 'navigate' }],
      ['/assets/app-12345678.js?private=1', {}], ['https://external.test/assets/app-12345678.js', {}],
      // Shaped like build output but not emitted by this build: an app's own assets stay on the network.
      ['/assets/sales-dashboard.js', {}], ['/assets/my-checkout.js', {}], ['/assets/app-87654321.js', {}],
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

  it('the source worker lists no build files until vite build writes them in', () => {
    expect(original.split('[/* evlet:build-assets */]')).toHaveLength(2);
  });

  it('keeps fingerprinted build files cache-first but always revalidates the icon', async () => {
    const w = worker();
    await w.request('/assets/app-12345678.js'); await w.request('/assets/app-12345678.js');
    expect(w.fetch).toHaveBeenCalledOnce();
    w.fetch.mockClear();
    await w.request('/assets/evlet-icon-blue.svg'); await w.request('/assets/evlet-icon-blue.svg');
    expect(w.fetch).toHaveBeenCalledTimes(2);
    expect(w.fetch).toHaveBeenLastCalledWith('https://evlet.test/assets/evlet-icon-blue.svg', { credentials: 'omit', cache: 'no-cache' });
    w.fetch.mockRejectedValueOnce(new Error('offline'));
    expect(await (await w.request('/assets/evlet-icon-blue.svg')).text()).toBe('public asset');
  });

  it('drops files of earlier builds when a new worker activates', async () => {
    const w = worker();
    for (const path of ['/assets/app-OLDBUILD.js', '/assets/app-12345678.js', '/assets/evlet-offline.html', '/assets/evlet-icon-blue.svg'])
      w.entries.set(path, new Response('kept'));
    let pending; w.listeners.activate({ waitUntil: value => { pending = value; } }); await pending;
    expect(w.entries.has('/assets/app-OLDBUILD.js')).toBe(false);
    expect(['/assets/app-12345678.js', '/assets/evlet-offline.html', '/assets/evlet-icon-blue.svg'].every(path => w.entries.has(path))).toBe(true);
  });

  it('turning the setting back on applies to a worker still running since it was turned off', async () => {
    const w = worker();
    await w.message('EVLET_DISABLE_STATIC_CACHE');
    expect(w.request('/assets/app-12345678.js')).toBeUndefined();
    await w.settled();
    expect(w.request('/assets/app-12345678.js')).toBeUndefined();
    // The page writes the marker, then tells the running worker.
    w.entries.set('/assets/evlet-cache-state', new Response('enabled'));
    await w.message('EVLET_ENABLE_STATIC_CACHE');
    await w.request('/assets/app-12345678.js');
    expect(w.cache.put).toHaveBeenCalledWith('https://evlet.test/assets/app-12345678.js', expect.anything());
  });

  it('reads the setting again on a passed-through request, without trusting a message to enable it', async () => {
    const w = worker();
    await w.message('EVLET_DISABLE_STATIC_CACHE');
    await w.message('EVLET_ENABLE_STATIC_CACHE');
    expect(w.request('/assets/app-12345678.js')).toBeUndefined();  // the marker still says disabled
    w.entries.set('/assets/evlet-cache-state', new Response('enabled'));
    expect(w.request('/assets/app-12345678.js')).toBeUndefined();
    await w.settled();
    expect(w.request('/assets/app-12345678.js')).toBeDefined();
  });

  it('gives navigations the offline notice while the asset cache is off, never caching them', async () => {
    const fetch = vi.fn(async () => new Response('authenticated HTML'));
    const w = worker(fetch);
    await w.message('EVLET_DISABLE_STATIC_CACHE');
    expect(await (await w.request('/', { mode: 'navigate' })).text()).toBe('authenticated HTML');
    w.entries.set('/assets/evlet-offline.html', new Response('generic offline notice'));
    fetch.mockRejectedValueOnce(new Error('offline'));
    expect(await (await w.request('/app/customer', { mode: 'navigate' })).text()).toBe('generic offline notice');
    expect(w.cache.put).not.toHaveBeenCalledWith(expect.stringContaining('/app'), expect.anything());
    expect(w.request('/custom-site', { mode: 'navigate' })).toBeUndefined();
    expect(w.request('/api/customer', { mode: 'navigate' })).toBeUndefined();
  });

  it('keeps the offline notice in a cache of its own build and drops earlier builds\' caches', async () => {
    const w = worker(undefined, ['evlet-static-v1', 'evlet-static-old', 'evlet-static-control', 'evlet-offline-v1', 'evlet-offline-old', 'another-app']);
    let pending; w.listeners.install({ waitUntil: value => { pending = value; } }); await pending;
    expect(w.caches.open).toHaveBeenCalledWith('evlet-offline-v1');
    expect(w.fetch).toHaveBeenCalledWith('/assets/evlet-offline.html', { credentials: 'omit', cache: 'reload' });
    w.listeners.activate({ waitUntil: value => { pending = value; } }); await pending;
    expect(w.caches.delete.mock.calls.map(call => call[0]).sort()).toEqual(['evlet-offline-old', 'evlet-static-old']);
  });

  it('the source worker carries exactly one build marker for vite build', () => {
    expect(original.split("/* evlet:build */'dev'")).toHaveLength(2);
  });
});
