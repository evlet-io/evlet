// Registered on every install-capable desk page (frontend/src/evlet/app/pwa.ts). Page navigations go to the network
// first and fall back to a fixed public offline notice; their responses are never stored. The static-asset cache
// is an explicit device setting. Never store sessions, API results, authenticated HTML, arbitrary URLs or
// mutations. A new version waits for existing tabs to close normally.
// This build's fingerprint, written in by `vite build`: a new release is a new worker with its own cache names.
const BUILD = /* evlet:build */'dev';
const PREFIX = 'evlet-static-', NOTICE_PREFIX = 'evlet-offline-';
const CACHE = `${PREFIX}${BUILD}`, NOTICE = `${NOTICE_PREFIX}${BUILD}`;
const CONTROL = `${PREFIX}control`, STATE = '/assets/evlet-cache-state';
const OFFLINE = '/assets/evlet-offline.html', ICON = '/assets/evlet-icon-blue.svg';
const MAX_ASSETS = 64;
// The fingerprinted files of this Evlet build, written in by `vite build` (frontend/vite.config.js).
// Only these are cache-first: any other /assets/ file, such as an app's own script, always goes to the network.
const BUILT = new Set([/* evlet:build-assets */]);

// The durable marker in CONTROL is the setting; `enabled` only remembers its last reading.
let enabled, changing = Promise.resolve();
async function readSetting() {
  await changing;
  try {
    const marker = await (await caches.open(CONTROL)).match(STATE);
    enabled = (await marker?.text()) === 'enabled';
  }
  catch { enabled = false; }
  return enabled;
}
const isEnabled = () => enabled !== undefined ? enabled : readSetting();

const fingerprinted = url => !url.search && BUILT.has(url.pathname);
const icon = url => !url.search && url.pathname === ICON;

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    // This one HTML file is fixed public content, served without runtime/session configuration.
    const response = await fetch(OFFLINE, { credentials: 'omit', cache: 'reload' });
    if (!response.ok || response.redirected) throw new Error('Offline notice unavailable');
    await (await caches.open(NOTICE)).put(OFFLINE, response);
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys())
      if (name.startsWith(PREFIX) ? name !== CACHE && name !== CONTROL : name.startsWith(NOTICE_PREFIX) && name !== NOTICE) await caches.delete(name);
    // Files of earlier builds are never requested again.
    try {
      const cache = await caches.open(CACHE);
      for (const key of await cache.keys()) {
        const path = new URL(key.url).pathname;
        if (path !== OFFLINE && path !== ICON && !BUILT.has(path)) await cache.delete(key);
      }
    } catch { /* Storage is optional. */ }
  })());
});

self.addEventListener('message', event => {
  const type = event.data?.type;
  if (type === 'EVLET_ENABLE_STATIC_CACHE') {
    // Sent after the page turned the setting on: a worker still running since it was turned off reads it again.
    event.waitUntil(readSetting());
    return;
  }
  if (type !== 'EVLET_DISABLE_STATIC_CACHE') return;
  enabled = false;
  changing = (async () => {
    await (await caches.open(CONTROL)).put(STATE, new Response('disabled'));
    for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CONTROL) await caches.delete(name);
  })().catch(() => {});
  event.waitUntil(changing);
});

async function remember(cache, key, response) {
  if (!cache || !enabled || !response.ok || response.redirected || response.type === 'opaque') return;
  try {
    await cache.put(key, response.clone());
    const keys = await cache.keys();
    const assets = keys.filter(item => new URL(item.url).pathname !== OFFLINE);
    for (const item of assets.slice(0, Math.max(0, assets.length - MAX_ASSETS))) await cache.delete(item);
  } catch { /* Full/blocked storage must not prevent a successful online response. */ }
}

self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  const navigation = request.mode === 'navigate';
  if (navigation) {
    if (url.pathname !== '/' && !/^\/(?:app|docs|settings|controls|eva|reports)(?:\/|$)/.test(url.pathname) && url.pathname !== '/login') return;
    // Network first, whatever the asset setting: successful HTML responses, including sign-in and error pages,
    // are never copied to Cache API. Only a failed request gets the public notice.
    event.respondWith((async () => {
      try { return await fetch(request); } catch {
        try { const notice = await (await caches.open(NOTICE)).match(OFFLINE); if (notice) return notice; } catch { /* Storage may be unavailable. */ }
        return new Response('Connect to open Evlet.', { status: 503, headers: { 'Content-Type': 'text/plain' } });
      }
    })());
    return;
  }
  if (!fingerprinted(url) && !icon(url)) return;
  if (enabled === false) {
    // Off: the browser handles the request. Read the setting again, so turning it back on applies without a restart.
    event.waitUntil?.(readSetting());
    return;
  }
  event.respondWith((async () => {
    if (!await isEnabled()) return fetch(request);
    let cache;
    try { cache = await caches.open(CACHE); } catch { /* Storage is optional. */ }
    if (icon(url)) {
      // Not fingerprinted: always ask the network, and use the kept copy only when it cannot answer.
      try {
        const response = await fetch(url.href, { credentials: 'omit', cache: 'no-cache' });
        await remember(cache, url.href, response);
        return response;
      } catch (error) {
        try { const known = await cache?.match(url.href); if (known) return known; } catch { /* Storage is optional. */ }
        throw error;
      }
    }
    try { const known = await cache?.match(url.href); if (known) return known; } catch { /* Storage is optional. */ }
    // Assets need no cookies or Authorization header; never retain a redirected response.
    const response = await fetch(url.href, { credentials: 'omit' });
    await remember(cache, url.href, response);
    return response;
  })());
});
